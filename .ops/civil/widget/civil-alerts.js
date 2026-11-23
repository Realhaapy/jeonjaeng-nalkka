/* Civilian SMS records. Shared by the static renderer and the public page. */
(function () {
  'use strict';

  const ministry = 'https://www.safekorea.go.kr/safekorea-kor/ctim/cmsg/';
  const listUrl = ministry + 'calamitySms.do?menuSn=34&cOcrcType=DST400&dsstrSeId=40&readYn=Y#content_area';
  const labels = {
    issued: '경보 발령', lifted: '경보 해제', correction: '정정 안내',
    exercise: '훈련 안내', notice: '민방공 안내'
  };
  const staleAfter = 10 * 60 * 1000;
  const day = 24 * 60 * 60 * 1000;
  const kstDate = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  });

  function escapeHtml(value) {
    const entities = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
    return String(value).replace(/[&<>"']/g, character => entities[character]);
  }

  function timestamp(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d{1,6})?)?(?:Z|[+-]\d\d:\d\d)$/.test(value)) {
      throw new Error('Invalid time');
    }
    const parsed = Date.parse(value);
    if (!Number.isFinite(parsed)) throw new Error('Invalid date');
    return parsed;
  }

  function sourceLink(raw, detail) {
    const url = new URL(raw);
    const path = '/safekorea-kor/ctim/cmsg/' + (detail ? 'calamitySmsView.do' : 'calamitySms.do');
    if (!['safekorea.go.kr', 'www.safekorea.go.kr'].includes(url.hostname) ||
        url.protocol !== 'https:' || url.port || url.username || url.password || url.pathname !== path) {
      throw new Error('Invalid official link');
    }
    if (detail && !/^\d+$/.test(url.searchParams.get('smsTrsmSn') || '')) {
      throw new Error('Missing official record number');
    }
    return url;
  }

  function shortText(value, max) {
    return typeof value === 'string' && value.length <= max && !/[\x00-\x1f\x7f]/.test(value);
  }

  function normalize(data, now = Date.now()) {
    if (!data || data.schema !== 1 || typeof data.complete !== 'boolean' ||
        !Array.isArray(data.items) || data.items.length > 500) throw new Error('Invalid record list');
    sourceLink(data.source_url, false);
    const checked = timestamp(data.checked_at), start = timestamp(data.window_start);
    if (checked > now + 60 * 1000 || checked <= start || checked - start > day + 60000) {
      throw new Error('Invalid coverage window');
    }
    const seen = new Set();
    const items = data.items.map(item => {
      const link = sourceLink(item.url, true);
      const id = String(item.id);
      const issued = timestamp(item.issued_at);
      if (!/^\d+$/.test(id) || id !== link.searchParams.get('smsTrsmSn') || seen.has(id) ||
          issued < start || issued > checked || !Object.hasOwn(labels, item.status) ||
          !shortText(item.region, 400) || !item.region.trim() || !shortText(item.issuer || '', 120)) {
        throw new Error('Invalid official record');
      }
      if (item.effective_at) timestamp(item.effective_at);
      seen.add(id);
      return {id, issued_at: item.issued_at, effective_at: item.effective_at || '',
        region: item.region, issuer: item.issuer || '', status: item.status, url: link.href};
    });
    items.sort((a, b) => timestamp(b.issued_at) - timestamp(a.issued_at) ||
      b.id.length - a.id.length || b.id.localeCompare(a.id));
    return {schema: 1, checked_at: data.checked_at, window_start: data.window_start,
      complete: data.complete, source_url: data.source_url, items};
  }

  function takeLatest(current, next) {
    if (!current) return next;
    const diff = timestamp(next.checked_at) - timestamp(current.checked_at);
    if (diff < 0 || (diff === 0 && current.complete && !next.complete)) return current;
    if (next.complete) return next;
    // A partial read cannot disprove messages already seen inside its window.
    const ids = new Set(next.items.map(item => item.id));
    const carried = current.items.filter(item =>
      !ids.has(item.id) && timestamp(item.issued_at) >= timestamp(next.window_start));
    return {...next, items: next.items.concat(carried).sort((a,b) =>
      timestamp(b.issued_at) - timestamp(a.issued_at))};
  }

  function displayTime(value) {
    const parts = Object.fromEntries(kstDate.formatToParts(timestamp(value)).map(p => [p.type, p.value]));
    return '<time datetime="' + escapeHtml(value) + '">' + parts.month + '.' + parts.day +
      ' ' + parts.hour + ':' + parts.minute + '</time>';
  }

  function renderItem(item) {
    const what = escapeHtml(labels[item.status]);
    const stamps = [displayTime(item.issued_at) + ' 문자 발송'];
    if (item.effective_at) {
      const verb = {issued: '발령', lifted: '해제'}[item.status] || '원문 기재';
      stamps.unshift(displayTime(item.effective_at) + ' ' + verb);
    }
    if (item.issuer) stamps.push(escapeHtml(item.issuer));
    return '<li data-status="' + item.status + '"><a href="' + escapeHtml(item.url) + '">' +
      '<strong>' + what + '</strong><span>' + escapeHtml(item.region) + '</span></a>' +
      '<small>' + stamps.join(' · ') + '</small></li>';
  }

  function rows(data) {
    if (!data) return '<p class="civil-tail">국민안전24에서 최근 발송 내역을 확인할 수 있습니다.</p>';
    if (!data.items.length) return '<p class="civil-tail">' +
      (data.complete ? '조회한 기간에 등록된 민방공 문자가 없습니다.' : '현재 확인된 목록에 민방공 문자가 없습니다.') + '</p>';
    const top = data.items.slice(0, 4).map(renderItem).join('');
    const rest = data.items.slice(4);
    const more = rest.length ? '<details><summary>앞선 발송 ' + rest.length + '건</summary>' +
      '<ol>' + rest.map(renderItem).join('') + '</ol></details>' : '';
    return '<ol>' + top + '</ol>' + more;
  }

  function stamp(data, failed, now = Date.now()) {
    if (!data) return failed ? '자동 조회 지연 · 공식 목록을 확인하세요.' : '공식 발송 기록 확인 중';
    const parts = ['공식 목록 조회 ' + displayTime(data.checked_at) + ' KST'];
    if (failed || now - timestamp(data.checked_at) > staleAfter) parts.push('갱신 지연 · 이전 조회 결과');
    if (!data.complete) parts.push('일부 목록만 확인');
    return parts.join(' · ');
  }

  function attachOfficialView(root) {
    // The background feed can be delayed by its scheduler. Open the source
    // after one reading interval while a cached export supplies the summary.
    const directAfter = root.dataset?.backup ? 120000 : 30000;
    const view = root.querySelector('[data-civil-official]');
    const box = view.querySelector('[data-civil-frame]');
    let frame, timer, userChose = false;
    view.querySelector('summary').addEventListener('click', () => { userChose = true; });
    function refresh() {
      if (!view.open || document.hidden) return;
      if (!frame) {
        frame = document.createElement('iframe');
        frame.title = '국민안전24 공식 민방공 재난문자 목록';
        frame.referrerPolicy = 'no-referrer';
        frame.loading = 'lazy';
        frame.setAttribute('sandbox', 'allow-forms allow-scripts allow-same-origin allow-popups');
        box.appendChild(frame);
      }
      const url = new URL(listUrl);
      // The filter form is taller than a phone's iframe. The end of the date
      // filter is the last stable official anchor immediately above the list.
      url.hash = 'endDate';
      url.searchParams.set('_', String(Math.floor(Date.now() / 30000)));
      frame.src = url.href;
    }
    function sync() {
      clearInterval(timer);
      if (!view.open) {
        if (frame) frame.remove();
        frame = null;
      } else if (!document.hidden) {
        refresh();
        timer = setInterval(refresh, 30000);
      }
    }
    view.hidden = false;
    view.addEventListener('toggle', sync);
    document.addEventListener('visibilitychange', sync);
    return function preferDirect(data, failed, now = Date.now()) {
      if (!view.open && !userChose && !document.hidden &&
          (failed || !data || !data.complete || now - timestamp(data.checked_at) >= directAfter)) {
        view.open = true;
      }
    };
  }

  function attach(root) {
    let latest = null;
    const seed = root.querySelector('[data-civil-seed]');
    if (seed) {
      try { latest = normalize(JSON.parse(seed.textContent)); } catch { /* Reload in browser. */ }
    }
    const list = root.querySelector('[data-civil-rows]');
    const note = root.querySelector('[data-civil-stamp]');
    const urls = [...new Set([root.dataset.feed, root.dataset.backup].filter(Boolean))];
    const preferDirect = attachOfficialView(root);
    let busy = false;
    function redraw(data) {
      if (data === latest) return;
      const expanded = list.querySelector('details')?.open;
      const activeHref = list.contains(document.activeElement) ? document.activeElement.getAttribute('href') : null;
      const newRows = rows(data);
      if (data && rows(latest) !== newRows) {
        list.innerHTML = newRows;
        const details = list.querySelector('details');
        if (expanded && details) details.open = true;
        if (activeHref) {
          const target = [...list.querySelectorAll('a')].find(a => a.getAttribute('href') === activeHref);
          if (target) target.focus({preventScroll: true});
        }
      }
      latest = data;
    }
    async function read(url) {
      const req = new URL(url, location.href);
      if (req.hostname === 'raw.githubusercontent.com') req.searchParams.set('fresh', String(Math.floor(Date.now() / 30000)));
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 7000);
      try {
        const response = await fetch(req, {cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal});
        if (!response.ok) throw new Error('Feed fetch failed');
        const body = await response.text();
        if (body.length > 500000) throw new Error('Feed too large');
        return normalize(JSON.parse(body));
      } finally { clearTimeout(timeout); }
    }
    async function update() {
      if (busy || document.hidden) return;
      preferDirect(latest, false);
      busy = true;
      let readOk = false;
      try {
        for (const url of urls) {
          try {
            const value = await read(url);
            redraw(takeLatest(latest, value));
            readOk = true;
            if (value.complete && Date.now() - timestamp(value.checked_at) < directAfter) break;
          } catch { /* Try the other authorized feed before reporting a delay. */ }
        }
      } finally {
        const text = stamp(latest, !readOk);
        if (note.innerHTML !== text) note.innerHTML = text;
        preferDirect(latest, !readOk);
        busy = false;
      }
    }
    document.addEventListener('visibilitychange', update);
    window.addEventListener('focus', update);
    window.addEventListener('pageshow', update);
    setInterval(update, 30000);
    update();
  }

  if (typeof module === 'object' && module.exports) {
    module.exports = {escapeHtml, listUrl, normalize, rows, stamp, takeLatest, attachOfficialView};
  } else {
    const root = document.querySelector('[data-civil-alerts]');
    if (root) attach(root);
  }
})();
