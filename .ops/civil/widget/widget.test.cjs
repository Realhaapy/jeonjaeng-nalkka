'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {normalize, rows, stamp, takeLatest, listUrl, attachOfficialView} = require('./civil-alerts.js');
const {install, render} = require('./inject.cjs');

const home = '<html><head></head><body data-mode="live"><main id="main" class="wrap"><h1>지금, 한반도</h1></main></body></html>';
const files = {css: 'civil-alerts.example.css', js: 'civil-alerts.example.js'};
const checked = '2026-11-24T08:10:30+09:00';
const now = Date.parse(checked);
const record = (id, status, time, effective) => ({id, status, issued_at: '2026-11-24T' + time + '+09:00',
  effective_at: effective ? '2026-11-24T' + effective + '+09:00' : undefined,
  region: '지역 <예시>', issuer: '발령기관',
  url: 'https://www.safekorea.go.kr/safekorea-kor/ctim/cmsg/calamitySmsView.do?menuSn=34&smsTrsmSn=' + id});
const sample = {
  schema: 1, complete: true, source_url: listUrl, checked_at: checked,
  window_start: '2026-11-23T08:10:30+09:00',
  items: [record('81', 'issued', '07:53:05', '07:52:00'), record('82','lifted','07:57:18','07:55:00')]
};

test('chronological records retain official issue and send times in Korea', () => {
  const data = normalize(sample, now), html = rows(data);
  assert.deepEqual(data.items.map(i => i.id), ['82', '81']);
  assert.ok(html.indexOf('경보 해제') < html.indexOf('경보 발령'));
  assert.match(html,/07:55<\/time> 해제/); assert.match(html,/07:57<\/time> 문자 발송/);
  assert.match(html,/지역 &lt;예시&gt;/); assert.doesNotMatch(html, /지역 <예시>/);
});
test('reject off-site details, IDs swapped, malformed coverage and clock skew', () => {
  for (const changed of [
    {...sample, source_url:'https://example.org/'}, {...sample, checked_at:'2026-11-24'},
    {...sample, window_start: '2025-11-23T08:10:30Z'}, {...sample, items:[sample.items[0],sample.items[0]]},
    {...sample, items:[{...sample.items[0], id:'5'}]},
    {...sample, items:[{...sample.items[0], url:'https://evil.example/safekorea.go.kr/'}]}
  ]) assert.throws(() => normalize(changed,now));
  assert.throws(() => normalize(sample, now - 65000));
});
test('incomplete and older responses do not lose known messages; empty is not all-clear', () => {
  const saved = normalize(sample, now);
  const later = normalize({...sample, checked_at:'2026-11-24T08:11:00+09:00', complete:false,
    items: [record('83','correction','08:10:40')]}, now+35000);
  const merged = takeLatest(saved, later);
  assert.deepEqual(merged.items.map(i=>i.id), ['83','82','81']);
  assert.equal(takeLatest(merged,saved),merged);
  assert.match(stamp(merged,true,now),/이전 조회 결과/);
  assert.match(stamp(merged,false,now),/일부 목록만/);
  assert.match(rows({...sample,items:[]}),/조회한 기간/);
  assert.match(stamp(saved,false,now+11*60000), /갱신 지연/);
});
test('static HTML includes original links, seeded records, and the same output on repeated injection', () => {
  const opts = {feedUrl:'https://example.org/public/feed',snapshot:sample};
  const once = render(home, opts,files,now);
  assert.equal(once,render(once,opts,files,now));
  assert.match(once,/<main[^>]*><!-- civil-alerts:panel:start --><section/);
  assert.match(once,/smsTrsmSn=82/); assert.match(once,/국민안전24 공식 목록/);
  assert.doesNotMatch(once,/<예시>/);
  assert.throws(()=>render(home.replace('data-mode="live"','data-mode="demo"'),opts,files,now));
  assert.throws(()=>render(home,{feedUrl:'javascript:bad'},files,now));
  assert.throws(()=>render(home,{feedUrl:'//evil.test'},files,now));
});
test('installed home does not alter surrounding public data or require a snapshot', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(),'civil-widget-'));
  try {
    fs.writeFileSync(path.join(tmp,'index.html'),home);
    fs.writeFileSync(path.join(tmp,'latest.json'),'{}');
    const options = {publicDir:tmp,feedUrl:'/official/feed.json'};
    install(options);
    const text=fs.readFileSync(path.join(tmp,'index.html'),'utf8');
    assert.equal(fs.readFileSync(path.join(tmp,'latest.json'),'utf8'),'{}');
    assert.match(text,/국민안전24 공식 목록/);
    assert.equal(fs.readdirSync(path.join(tmp,'assets')).length,2);
    install(options);
    assert.equal(text,fs.readFileSync(path.join(tmp,'index.html'),'utf8'));
  } finally { fs.rmSync(tmp,{recursive:true}); }
});

test('qualifies only the official hourly SMS row and keeps its original times and figures', () => {
  const href = 'https://www.safekorea.go.kr/safekorea-kor/ctim/cmsg/calamitySms.do?menuSn=34';
  const makeRow = url => '<li class="official-row"><a href="' + url +
    '" class="official-title">발령기관이 바꾼 제목 <svg><path/></svg></a>\n' +
    '<strong class="official-value">민방공 관련 0건</strong><p>전체 문자 40건 · 07:32 KST</p></li>';
  const unrelated = makeRow('https://other.example.org/');
  const opts = {feedUrl:'/feed.json'};
  const output = render(home.replace('</main>', makeRow(href) + unrelated + '</main>'), opts, files);
  assert.match(output,/정시 수집 당시 · <\/span>민방공 관련 0건/);
  assert.match(output,/<a href="#civil-alerts-title">최신 민방공 발송 기록<\/a>/);
  assert.equal(output.split('전체 문자 40건 · 07:32 KST').length, 3);
  assert.ok(output.includes(unrelated));
  assert.equal(render(output,opts,files), output);
});

test('official iframe loads only while open; closing or hiding the tab stops its 30s timer', () => {
  let toggle, visibility, tick, frame, choose;
  const box = {appendChild(f) { frame = f; }};
  const details = {hidden: true, open: false, querySelector: selector => selector === 'summary'
    ? {addEventListener: (_, fn) => { choose = fn; }} : box,
    addEventListener: (event, fn) => { if (event === 'toggle') toggle = fn; }};
  const doc = {hidden: false, addEventListener: (event, fn) => { if (event === 'visibilitychange') visibility = fn; },
    createElement: () => ({setAttribute() {}, remove() { frame = null; }})};
  const before = {document: global.document, setInterval: global.setInterval, clearInterval: global.clearInterval};
  try {
    global.document = doc;
    global.setInterval = (fn, ms) => { assert.equal(ms, 30000); tick = fn; return 1; };
    global.clearInterval = () => { tick = null; };
    const preferDirect = attachOfficialView({querySelector: () => details});
    assert.equal(details.hidden, false); assert.equal(frame, undefined);
    preferDirect(sample, false, now + 29999); assert.equal(details.open, false);
    preferDirect(sample, false, now + 30000); assert.equal(details.open, true); toggle();
    preferDirect(sample, false, now); assert.equal(details.open, true);
    assert.equal(new URL(frame.src).hostname, 'www.safekorea.go.kr');
    assert.equal(new URL(frame.src).searchParams.get('dsstrSeId'), '40');
    assert.equal(new URL(frame.src).hash, '#endDate');
    assert.equal(typeof tick, 'function');
    doc.hidden = true; visibility(); assert.equal(tick, null);
    doc.hidden = false; visibility(); assert.equal(typeof tick, 'function');
    choose(); details.open = false; toggle(); assert.equal(tick, null); assert.equal(frame, null);
    preferDirect(null, true, now); assert.equal(details.open, false);
    const secondVisit = attachOfficialView({querySelector: () => details});
    doc.hidden = true; secondVisit(null, true, now); assert.equal(details.open, false);
    doc.hidden = false; secondVisit(null, false, now); assert.equal(details.open, true);
    details.open = false;
    const thirdVisit = attachOfficialView({querySelector: () => details});
    thirdVisit(sample, true, now); assert.equal(details.open, true);
  } finally {
    global.setInterval = before.setInterval; global.clearInterval = before.clearInterval;
    if (before.document === undefined) delete global.document; else global.document = before.document;
  }
});
