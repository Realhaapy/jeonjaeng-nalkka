'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {escapeHtml, listUrl, normalize, rows, stamp} = require('./civil-alerts.js');

function feedLocation(raw) {
  if (typeof raw !== 'string' || !raw || /[\s\\<>]/.test(raw)) throw new Error('Invalid feed URL');
  if (/^\/(?!\/)/.test(raw)) return raw;
  const url = new URL(raw);
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) throw new Error('Use HTTPS feed URLs');
  return url.href;
}

function block(name, html) {
  return '<!-- civil-alerts:' + name + ':start -->' + html + '<!-- civil-alerts:' + name + ':end -->';
}

function clearBlock(html, name) {
  const begin = '<!-- civil-alerts:' + name + ':start -->';
  const end = '<!-- civil-alerts:' + name + ':end -->';
  if (!html.includes(begin) && !html.includes(end)) return html;
  if (html.split(begin).length !== 2 || html.split(end).length !== 2 || html.indexOf(begin) > html.indexOf(end)) {
    throw new Error('Unexpected existing civil alert markup');
  }
  return html.slice(0, html.indexOf(begin)) + html.slice(html.indexOf(end) + end.length);
}

function render(index, options, files, now = Date.now()) {
  const primary = feedLocation(options.feedUrl);
  const backup = options.backupUrl ? feedLocation(options.backupUrl) : '';
  const data = options.snapshot ? normalize(options.snapshot, now) : null;
  const seed = data ? '<script type="application/json" data-civil-seed>' +
    JSON.stringify(data).replace(/</g, '\\u003c') + '</script>' : '';
  const region = '<section class="civil-alerts" data-civil-alerts data-feed="' + escapeHtml(primary) +
    '" data-backup="' + escapeHtml(backup) + '" aria-labelledby="civil-alerts-title">' +
    '<header><h2 id="civil-alerts-title">민방공 재난문자</h2><a href="' + escapeHtml(listUrl) +
    '">국민안전24 공식 목록</a></header>' +
    '<details data-civil-official hidden><summary>국민안전24 실시간 목록</summary>' +
    '<p class="civil-tail">펼쳐 둔 동안 공식 페이지를 30초마다 다시 불러옵니다.</p><div data-civil-frame></div></details>' +
    '<div data-civil-rows>' + rows(data) + '</div>' +
    '<p class="civil-tail" data-civil-stamp role="status">' + stamp(data, false, now) + '</p>' +
    '<noscript><p class="civil-tail">최신 발송 내용은 국민안전24 공식 목록에서 확인하세요.</p></noscript>' + seed + '</section>';
  const assets = '<link rel="stylesheet" href="/assets/' + files.css + '">' +
    '<script defer src="/assets/' + files.js + '"></script>';
  const clean = clearBlock(clearBlock(index, 'assets'), 'panel');
  const main = /<main\b(?=[^>]*\bid=["']main["'])[^>]*>/gi;
  if (clean.match(main)?.length !== 1 || clean.match(/<\/head>/gi)?.length !== 1 ||
      !/<body\b[^>]*\bdata-mode=["']live["']/i.test(clean)) {
    throw new Error('Expected the live homepage with one main element');
  }
  return clean.replace(/<\/head>/i, () => block('assets', assets) + '</head>')
    .replace(main, match => match + block('panel', region));
}

function install(options) {
  const dir = path.resolve(options.publicDir);
  const indexPath = path.join(dir, 'index.html');
  const input = fs.readFileSync(indexPath, 'utf8');
  const names = {}, contents = {};
  for (const ext of ['js', 'css']) {
    contents[ext] = fs.readFileSync(path.join(__dirname, 'civil-alerts.' + ext));
    const hash = crypto.createHash('sha256').update(contents[ext]).digest('hex').slice(0, 16);
    names[ext] = 'civil-alerts.' + hash + '.' + ext;
  }
  const output = render(input, options, names);
  fs.mkdirSync(path.join(dir, 'assets'), {recursive: true});
  for (const ext of ['js', 'css']) fs.writeFileSync(path.join(dir, 'assets', names[ext]), contents[ext]);
  if (output !== input) {
    const tmp = indexPath + '.civil-' + process.pid;
    fs.writeFileSync(tmp, output, {flag: 'wx'});
    fs.renameSync(tmp, indexPath);
  }
}

if (require.main === module) {
  const {parseArgs} = require('node:util');
  const {values} = parseArgs({options: {
    'public-dir': {type: 'string'}, 'feed-url': {type: 'string'},
    'backup-url': {type: 'string'}, snapshot: {type: 'string'}
  }});
  if (!values['public-dir'] || !values['feed-url']) throw new Error('Supply --public-dir and --feed-url');
  install({publicDir: values['public-dir'], feedUrl: values['feed-url'], backupUrl: values['backup-url'],
    snapshot: values.snapshot ? JSON.parse(fs.readFileSync(values.snapshot, 'utf8')) : null});
}
module.exports = {install, render};
