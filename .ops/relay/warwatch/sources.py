"""Bounded public-source discovery. No article text is scraped by default."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import json
import re
import time
import io
import zipfile
from urllib.parse import urljoin, urlsplit, urlunsplit, urlencode, parse_qs, parse_qsl
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from .store import valid_timestamp, canonical_url, utcnow

USER_AGENT = "JeonjaengNalkka/0.1 (+local noncommercial source-link collector)"

# Discovery/triage hints only: these do not establish a casualty, hostile act or
# a link to North Korea. An editor must read the source before publishing a fact.
MILITARY_CONTEXT = re.compile(
    r"북한|북측|北|김정은|남북|한반도|한미|한일|국방|군사|합참|합동참모|육군|해군|공군|해병|"
    r"주한미군|유엔사|청해\s*부대|군부대|전방\s*부대|접경|비무장지대|DMZ|"
    r"장병|군인|병사|훈련병|병장|군\s*간부|부사관|하사|중사|장교|"
    r"(?:이병|일병|상병)(?=[\s·….,\"'“”])|지뢰|미사일|ICBM|SLBM|軍|"
    r"north\s*korea|south\s*korea|\bdprk\b|\brok\b|\busfk\b", re.I)
MILITARY_INCIDENT = re.compile(
    r"사망|숨졌|숨져|숨진|순직|(?<![가-힣])전사(?=$|[^가-힣]|자|했|한|할|함|하|해|했다|했다고)|부상|중상|사상자|총상|피격|"
    r"지뢰|폭발|총격|포격|교전|침투|침범|월북|월남|철책|군사분계선|공중\s*충돌|"
    r"(?:미사일|ICBM|SLBM).*발사|발사.*(?:미사일|ICBM|SLBM)|"
    r"\bcasualt|\bkilled\b|\binjured\b", re.I)
FOREIGN_CONFLICT = re.compile(r"이란|이스라엘|우크라이나|러시아|가자|레바논|시리아|수단|미얀마|대만|인도|파키스탄|태국|캄보디아|아프가니스탄|예멘|후티|미국|중국|일본")
KOREAN_CONTEXT = re.compile(r"한국|대한민국|북한|북측|北|김정은|남북|한반도|한미|한일|주한|국군|청해\s*부대|우리\s*군|접경|DMZ|\bkorea|\bdprk\b|\brok\b|\busfk\b", re.I)


def news_priority(document):
    """Return a review hint (2 incident, 1 military, 0 other), never a verdict."""
    title = str(document.get("title", ""))
    if FOREIGN_CONFLICT.search(title) and not KOREAN_CONTEXT.search(title):
        return 0
    if not MILITARY_CONTEXT.search(title):
        return 0
    # In Korean, 부상 also means a vice minister; that title is not an injury.
    incident_title = re.sub(r"(?:외무성|외교부)\s*(?:제\d+)?부상", "", title)
    return 2 if MILITARY_INCIDENT.search(incident_title) else 1


class SourceDocuments(list):
    def __init__(self, values, complete=True, *, listing_count=None, total_count=None, has_more=False, coverage_note=None, observation=None):
        super().__init__(values)
        self.complete = complete
        self.listing_count = listing_count
        self.total_count = total_count
        self.has_more = has_more
        self.coverage_note = coverage_note
        self.observation = observation


def load_config(path):
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config.get("sources"), list):
        raise ValueError("sources 목록이 필요합니다.")
    ids = set()
    for s in config["sources"]:
        if s["id"] in ids:
            raise ValueError("중복 source id")
        ids.add(s["id"])
        if urlsplit(s["fetch_url"]).scheme != "https":
            raise ValueError("자료 수집은 HTTPS만 허용합니다.")
    return config


def fetch_text(url, client=None):
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT})
    try:
        for attempt in range(3):
            try:
                response = client.get(url)
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = urljoin(url, response.headers.get("location", ""))
                    if urlsplit(target).hostname != urlsplit(url).hostname or urlsplit(target).scheme != "https":
                        raise ValueError("동일 출처 HTTPS 이외 리다이렉트는 자동 수집하지 않습니다.")
                    response = client.get(target)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    retry = response.headers.get("retry-after", "")
                    wait = 2 ** attempt
                    if retry.isdigit():
                        wait = int(retry)
                    elif retry:
                        try:
                            resume_at = parsedate_to_datetime(retry)
                            if resume_at.tzinfo is None:
                                resume_at = resume_at.replace(tzinfo=timezone.utc)
                            wait = max(0, (resume_at - datetime.now(timezone.utc)).total_seconds())
                        except (TypeError, ValueError, OverflowError):
                            pass
                    # Leave a longer server-requested delay to a later run;
                    # never shorten it and retry this host too early.
                    if wait >= 10:
                        response.raise_for_status()
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                if len(response.content) > 3_000_000:
                    raise ValueError("수집 응답이 3MB 제한을 초과했습니다.")
                return response.text
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise ValueError("수집 재시도 실패")
    finally:
        if owned:
            client.close()


def parse_govuk(text, source):
    data = json.loads(text)
    if data.get("base_path") != "/foreign-travel-advice/south-korea" or not isinstance(data.get("details"), dict):
        raise ValueError("GOV.UK 문서 구조가 예상과 다릅니다.")
    details = data["details"]
    pieces = [str(data.get("description", "")), str(details.get("change_description", ""))]
    for part in details.get("parts", []):
        pieces.extend([str(part.get("title", "")), BeautifulSoup(part.get("body", ""), "html.parser").get_text(" ", strip=True)])
    timestamp = data.get("public_updated_at")
    if not timestamp and details.get("change_history"):
        timestamp = details["change_history"][0].get("public_timestamp")
    return [{"title": "한국 여행정보 — " + data.get("title", "South Korea"), "url": source["url"],
             "published_at": valid_timestamp(timestamp), "published_precision": "minute" if timestamp else "unknown", "body": "\n".join(pieces),
             "revision_hint": details.get("change_description", "")}]


def parse_korea(text, source):
    soup = BeautifulSoup(text, "html.parser")
    listing = soup.select_one('.article_body .list_type')
    result = soup.select_one('.article_body .sch_brief .result')
    total_match = re.search(r'총\s*([\d,]+)\s*건', result.get_text(' ', strip=True)) if result else None
    if listing is None or total_match is None:
        raise ValueError("보도자료 검색 목록 구조를 확인하지 못했습니다.")
    total = int(total_match[1].replace(',', ''))
    page = max(1, int(parse_qs(urlsplit(source['fetch_url']).query).get('pageIndex', ['1'])[0]))
    # This source displays 20 unfiltered results per page. A relevant-document
    # count cannot determine whether another search page exists.
    has_more = page * 20 < total
    links = [a for a in listing.select("a[href]") if re.search(r"(?:pressRelease|policyBriefing)View\.do\?[^\s]*newsId=", a.get("href", ""))]
    if not links:
        empty = listing.select_one('li.no_data')
        empty_text = re.sub(r'\s+', '', empty.get_text()) if empty else ''
        if empty_text in {'등록된정보가없습니다.', '검색결과가없습니다.'} and (total == 0 or (page - 1) * 20 >= total):
            return SourceDocuments([], listing_count=0, total_count=total, has_more=False)
        raise ValueError("보도자료 목록 링크가 없어 정상 점검으로 처리하지 않습니다.")
    if total == 0:
        raise ValueError("보도자료 검색 건수와 목록이 일치하지 않습니다.")
    documents = []
    seen = set()
    keywords = re.compile(r"북한|남북|한반도|미사일|국방|합참|통일부|외교부|한미|군사|정전|유엔사|육군|해군|공군|해병|장병|부사관|하사|지뢰|접경")
    for a in links:
        container = a.find_parent("li") or a
        context = container.get_text(" ", strip=True)
        title_node = a.select_one(".title, .tit, strong")
        title = re.sub(r"\s+", " ", (title_node or a).get_text("", strip=True))
        url = trusted_detail_url(urljoin(source["fetch_url"], a["href"]), 'www.korea.kr', '/briefing/')
        if not keywords.search(context) or url in seen:
            continue
        seen.add(url)
        # Dates shown on listing are publication dates, never event occurrence dates.
        match = re.search(r"(20\d{2})[.\-/](\d{2})[.\-/](\d{2})", context)
        stamp = f"{match[1]}-{match[2]}-{match[3]}T00:00:00+09:00" if match else None
        agency = container.select_one('.source')
        lead = a.select_one('.lead')
        documents.append({"title": title[:500], "url": url, "published_at": valid_timestamp(stamp), "published_precision": "day" if stamp else "unknown", "body": "",
                          "listing_excerpt": lead.get_text(' ', strip=True) if lead else '',
                          "agency_context": agency.get_text(' ', strip=True) if agency else context})
    return SourceDocuments(documents[:30], complete=len(documents) <= 30,
                           listing_count=len(links), total_count=total, has_more=has_more)


def parse_jso(text, source):
    soup = BeautifulSoup(text, "html.parser")
    links = [a for a in soup.select("a[href]") if ".pdf" in a["href"].lower()
             and urlsplit(urljoin(source["fetch_url"], a["href"])).hostname == "www.mod.go.jp"
             and "/js/pdf/" in urljoin(source["fetch_url"], a["href"])]
    if not links:
        raise ValueError("일본 방위성 PDF 목록 구조를 확인하지 못했습니다.")
    documents = []
    seen = set()
    keywords = re.compile(r"korea|dprk|ballistic|missile", re.I)
    for a in links:
        title = a.get_text(" ", strip=True)
        if not keywords.search(title):
            continue
        url = urljoin(source["fetch_url"], a["href"])
        if url in seen:
            continue
        current_year = str(datetime.now(timezone.utc).year)
        if not re.search(r"/js/pdf/" + current_year + r"/", url):
            continue
        seen.add(url)
        match = re.search(r"/p(20\d{2})(\d{2})(\d{2})_", url)
        stamp = f"{match[1]}-{match[2]}-{match[3]}T00:00:00+09:00" if match else None
        documents.append({"title": title, "url": url, "published_at": valid_timestamp(stamp), "published_precision": "day" if stamp else "unknown", "body": ""})
    return SourceDocuments(documents[:30], complete=len(documents) <= 30)


def parse_korea_briefings(text, source):
    """Parse the 30-row ministry transcript table, including generic briefing titles."""
    soup = BeautifulSoup(text, 'html.parser')
    table = soup.select_one('.article_body table.table_list')
    result = soup.select_one('.article_body .sch_brief .result')
    match = re.search(r'총\s*([\d,]+)\s*건', result.get_text(' ', strip=True)) if result else None
    if table is None or match is None:
        raise ValueError('부처 브리핑 목록 구조를 확인하지 못했습니다.')
    headers = [x.get_text('', strip=True) for x in table.select('thead th')]
    if headers[:4] != ['기관명', '주제', '등록일', '발표자']:
        raise ValueError('부처 브리핑 목록 항목이 변경되었습니다.')
    total = int(match[1].replace(',', ''))
    page = max(1, int(parse_qs(urlsplit(source['fetch_url']).query).get('pageIndex', ['1'])[0]))
    rows = table.select('tbody tr')
    if total == 0 or (page - 1) * 30 >= total:
        if len(rows) == 1 and re.sub(r'\s+', '', rows[0].get_text()) in {'등록된정보가없습니다.', '검색결과가없습니다.'}:
            return SourceDocuments([], listing_count=0, total_count=total, has_more=False)
        raise ValueError('부처 브리핑 검색 건수와 목록이 일치하지 않습니다.')
    if not rows or len(rows) > 30:
        raise ValueError('부처 브리핑 목록 행을 확인하지 못했습니다.')
    agencies = set(source.get('agencies', ['국방부', '통일부', '외교부']))
    documents, seen = [], set()
    for row in rows:
        cells = row.find_all('td', recursive=False)
        if len(cells) != 4:
            raise ValueError('부처 브리핑 목록 행 구조를 확인하지 못했습니다.')
        agency = cells[0].get_text(' ', strip=True)
        if agency not in agencies:
            continue
        link = cells[1].select_one('a[href]:has(strong)')
        stamp = re.fullmatch(r'(20\d{2})\.(\d{2})\.(\d{2})', cells[2].get_text('', strip=True))
        if link is None or stamp is None:
            raise ValueError('부처 브리핑 제목·게시일을 확인하지 못했습니다.')
        url = trusted_detail_url(urljoin(source['fetch_url'], link['href']), 'www.korea.kr', '/briefing/policyBriefingView.do')
        if urlsplit(url).path != '/briefing/policyBriefingView.do' or not re.fullmatch(r'\d+', parse_qs(urlsplit(url).query).get('newsId', [''])[0]):
            raise ValueError('부처 브리핑 상세 주소를 확인하지 못했습니다.')
        url = canonical_url(url)
        if url in seen:
            continue
        seen.add(url)
        title = link.select_one('strong').get_text(' ', strip=True)
        if not title:
            raise ValueError('부처 브리핑 제목이 비어 있습니다.')
        documents.append({'title': title[:500], 'url': url, 'agency_context': agency,
                          'published_at': valid_timestamp(f'{stamp[1]}-{stamp[2]}-{stamp[3]}T00:00:00+09:00'),
                          'published_precision': 'day', 'body': ''})
    return SourceDocuments(documents, listing_count=len(rows), total_count=total, has_more=page * 30 < total)


def parse_rss_metadata(text, source):
    """Bounded RSS discovery: keep headline/link/date; never description or media."""
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
        raise ValueError("외부 선언이 있는 RSS는 수집하지 않습니다.")
    root = ElementTree.fromstring(text)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None or not channel.findtext("title"):
        raise ValueError("정상 RSS 2.0 목록을 확인하지 못했습니다.")
    items = channel.findall("item")
    documents, seen = [], set()
    scan_limit = min(200, max(1, int(source.get("max_feed_items", 30))))
    document_limit = min(100, max(1, int(source.get("max_documents", 30))))
    military_only = source.get("topic_filter") == "korean_military"
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(source.get("lookback_days", 30))))
    skipped_invalid = 0
    for item in items[:scan_limit]:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        parts = urlsplit(url)
        try:
            trusted = parts.port in (None, 443) and not parts.username and not parts.password
        except ValueError:
            trusted = False
        if not title or not trusted or parts.scheme != "https" or parts.hostname not in source.get("allowed_hosts", []):
            skipped_invalid += 1
            continue
        if source.get("path_prefix") and not parts.path.startswith(source["path_prefix"]):
            continue
        if military_only and not news_priority({"title": title}):
            continue
        # A publisher's tracking parameters must not create distinct documents.
        if source.get("query_keys") is not None:
            query = [(k, v) for k, v in parse_qsl(parts.query) if k in source["query_keys"]]
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        url = canonical_url(url)
        if url in seen:
            continue
        seen.add(url)
        stamp = None
        try:
            dt = parsedate_to_datetime(item.findtext("pubDate") or "")
            if dt.tzinfo is not None:
                stamp = valid_timestamp(dt.isoformat())
        except (ValueError, TypeError, OverflowError):
            pass
        if military_only and stamp and datetime.fromisoformat(stamp.replace("Z", "+00:00")) < cutoff:
            continue
        documents.append({"title": title[:500], "url": url, "published_at": stamp,
                          "published_precision": "minute" if stamp else "unknown", "body": ""})
    if military_only:
        documents.sort(key=lambda d: (news_priority(d), d.get("published_at") or ""), reverse=True)
    issues = []
    if len(items) > scan_limit:
        issues.append("RSS 항목 상한으로 최신 목록 일부만 확인했습니다.")
    if len(documents) > document_limit:
        issues.append("관련 자료 처리 상한으로 일부 링크만 저장했습니다.")
    if skipped_invalid:
        issues.append("일부 RSS 항목의 제목 또는 출처 주소를 확인하지 못했습니다.")
    return SourceDocuments(documents[:document_limit], complete=not issues,
                           listing_count=min(len(items), scan_limit), total_count=len(items),
                           has_more=len(items) > scan_limit or len(documents) > document_limit,
                           coverage_note=" ".join(issues) or None)


def trusted_detail_url(url, host, path_prefix):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname != host or p.username or p.password or p.port not in (None, 443) or not p.path.startswith(path_prefix):
        raise ValueError('본문은 지정한 공식 기관의 HTTPS 경로에서만 수집합니다.')
    return url


def korea_rights(html, url):
    """Only the actual article's explicit derivative-allowed KOGL notice enables text use."""
    trusted_detail_url(url, 'www.korea.kr', '/briefing/')
    soup = BeautifulSoup(html, 'html.parser')
    allowed = soup.select('img[src*="open_type_01"], img[src*="open_type_02"]')
    prohibited = soup.select('img[src*="open_type_03"], img[src*="open_type_04"]')
    if prohibited or not allowed:
        return None
    license_id = 'KOGL-2-noncommercial' if 'open_type_02' in str(allowed[0]) else 'KOGL-1'
    return {'license': license_id, 'url': canonical_url(url), 'checked_at': utcnow(), 'scope': 'text only; no images; attribution required',
            'permissions': {'fetch':'allowed','llm':'allowed','summary':'allowed','retention':'allowed'}}


def extract_hwpx(blob):
    if len(blob) > 3_000_000:
        raise ValueError('첨부 원문 크기 제한 초과')
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if re.fullmatch(r'Contents/section\d+\.xml', n)]
        if not names or sum(z.getinfo(n).file_size for n in names) > 8_000_000:
            raise ValueError('첨부 원문 XML 크기 또는 구조를 확인할 수 없습니다.')
        texts = []
        for n in names:
            data = z.read(n)
            if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I):
                raise ValueError('외부 XML 선언은 처리하지 않습니다.')
            root = ElementTree.fromstring(data)
            paragraphs = [''.join(e.itertext()) for e in root.iter() if e.tag.rsplit('}',1)[-1] == 'p']
            texts.extend(paragraphs)
        text = '\n'.join(texts)
        # Contact tables are irrelevant to event extraction and never needed for public summaries.
        text = re.split(r'담당\s*부서|담당자\s*:', text)[0]
        return text[:24000].strip()


def enrich_korea(document, client):
    url = canonical_url(document['url'])
    trusted_detail_url(url, 'www.korea.kr', '/briefing/')
    html = fetch_text(url, client)
    rights = korea_rights(html, url)
    if not rights:
        return document
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.select_one('.view_cont #content')
    body = content.get_text('\n', strip=True) if content and not content.select('iframe') else ''
    body_origin = 'article_html'
    if urlsplit(url).path == '/briefing/policyBriefingView.do':
        transcript = soup.select_one('.article_body .view_cont')
        if transcript:
            for node in transcript.select('.movie, video, audio, iframe, img, figure, script, style'):
                node.decompose()
            body = transcript.get_text('\n', strip=True)
            body_origin = 'briefing_transcript'
    if len(body) < 80:
        link = next((a for a in soup.select('.filedown a[href]') if '.hwpx' in a.get_text().lower()), None)
        if link:
            attachment = trusted_detail_url(urljoin(url, link['href']), 'www.korea.kr', '/common/download.do')
            r = client.get(attachment)
            r.raise_for_status()
            if len(r.content) <= 3_000_000:
                body = extract_hwpx(r.content)
                body_origin = 'article_hwpx'
    if len(body) < 80:
        body = document.get('listing_excerpt','')[:24000]
        body_origin = 'listing_excerpt'
    if len(body) < 80:
        return document
    result = dict(document, url=url, body=body, rights=rights, permissions=rights['permissions'], rights_verified=True,
                  body_origin=body_origin)
    result.pop('listing_excerpt', None)
    return result


def _source_failure_kind(exc):
    """Expose only a status code or exception class, never request/body details."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f'HTTP {exc.response.status_code}'
    return type(exc).__name__


def _korea_host_unavailable(exc):
    # fetch_text has already exhausted its bounded retries, or deferred a
    # Retry-After. More keywords/details on the same host cannot fix this run.
    return (isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError))
            or (isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code in {429, 500, 502, 503, 504}))


def fetch_korea_history(source, client):
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).date()
    start = today - timedelta(days=int(source.get('lookback_days',30)))
    found, seen, failures = [], set(), 0
    successful_pages, issues = 0, []
    last_error, host_unavailable = None, False
    queries = source.get('queries',['북한','한미','훈련','미사일'])
    pages = min(3,max(1,int(source.get('history_pages',2))))
    for word in queries:
        for page in range(1,pages+1):
            url = source['fetch_url']+'?'+urlencode({'srchWord':word,'startDate':str(start),'endDate':str(today),'pageIndex':page})
            try:
                html = fetch_text(url,client)
                docs = parse_korea(html,dict(source,fetch_url=url))
            except (ValueError, httpx.HTTPError) as exc:
                # Preserve results from healthy queries, but never claim full coverage.
                last_error = exc
                issues.append(f'일부 검색 목록을 확인하지 못했습니다 ({_source_failure_kind(exc)}).')
                host_unavailable = _korea_host_unavailable(exc)
                break
            successful_pages += 1
            if not docs.complete:
                issues.append('목록 처리 상한으로 일부 자료만 확인했습니다.')
            for doc in docs:
                key = canonical_url(doc['url'])
                if key in seen: continue
                if not re.search(r'국방부|외교부|통일부|합동참모|방위사업청|육군|해군|공군|해병대',doc.get('agency_context','')): continue
                seen.add(key); doc['url']=key;found.append(doc)
            if not docs.has_more:
                break
            if page == pages:
                issues.append('검색 페이지 상한으로 목록 일부만 확인했습니다.')
        if host_unavailable:
            break
    if not successful_pages:
        detail = f' ({_source_failure_kind(last_error)})' if last_error is not None else ''
        raise ValueError(f'정책브리핑 검색 목록을 확인하지 못했습니다{detail}.') from last_error
    found.sort(key=lambda d:d.get('published_at') or '',reverse=True)
    limit = min(60,int(source.get('max_documents',40)))
    # Reserve a bounded share for casualty/border leads without letting an old
    # incident fill every slot and starve newly published official statements.
    priority_slots = min(limit, max(0, int(source.get('priority_document_slots', 10))))
    priority = [d for d in found if news_priority(d) == 2][:priority_slots]
    priority_urls = {d['url'] for d in priority}
    selected = priority + [d for d in found if d['url'] not in priority_urls][:limit-len(priority)]
    docs=[]
    for document in selected:
        if host_unavailable:
            docs.append(document)
            continue
        try: docs.append(enrich_korea(document,client))
        except (ValueError,httpx.HTTPError,zipfile.BadZipFile,ElementTree.ParseError) as e:
            failures+=1
            document['body']=''; document['body_error']=type(e).__name__
            docs.append(document)
            if _korea_host_unavailable(e):
                host_unavailable = True
                issues.append(f'상세 자료 연결을 확인하지 못했습니다 ({_source_failure_kind(e)}).')
    if host_unavailable:
        issues.append('연결 오류로 동일 자료원의 남은 목록·본문 요청을 중단했습니다.')
    if len(found) > limit:
        issues.append('자료 처리 상한으로 일부 자료만 확인했습니다.')
    if failures:
        issues.append('일부 상세 자료를 확인하지 못해 원문 링크만 저장했습니다.')
    return SourceDocuments(docs, complete=not issues, coverage_note=' '.join(dict.fromkeys(issues)) or None)


def fetch_korea_briefings(source, client):
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).date()
    start = today - timedelta(days=min(30, max(1, int(source.get('lookback_days', 7)))))
    pages = min(3, max(1, int(source.get('history_pages', 2))))
    limit = min(30, max(1, int(source.get('max_documents', 20))))
    found, seen, issues, successes = [], set(), [], 0
    last_error, host_unavailable = None, False
    for page in range(1, pages + 1):
        url = source['fetch_url'] + '?' + urlencode({'startDate': str(start), 'endDate': str(today), 'pageIndex': page})
        try:
            docs = parse_korea_briefings(fetch_text(url, client), dict(source, fetch_url=url))
        except (ValueError, httpx.HTTPError) as exc:
            last_error = exc
            issues.append(f'일부 부처 브리핑 목록을 확인하지 못했습니다 ({_source_failure_kind(exc)}).')
            host_unavailable = _korea_host_unavailable(exc)
            break
        successes += 1
        for doc in docs:
            date = datetime.fromisoformat(doc['published_at'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=9))).date()
            if not start <= date <= today:
                issues.append('요청 기간 밖의 브리핑은 제외했습니다.')
                continue
            if doc['url'] not in seen:
                seen.add(doc['url']); found.append(doc)
        if not docs.has_more:
            break
        if page == pages:
            issues.append('부처 브리핑 검색 페이지 상한으로 일부만 확인했습니다.')
    if not successes:
        detail = f' ({_source_failure_kind(last_error)})' if last_error is not None else ''
        raise ValueError(f'부처 브리핑 목록을 확인하지 못했습니다{detail}.') from last_error
    found.sort(key=lambda d: d['published_at'], reverse=True)
    if len(found) > limit:
        issues.append('부처 브리핑 자료 처리 상한으로 일부만 확인했습니다.')
    result = []
    for doc in found[:limit]:
        if host_unavailable:
            result.append(doc)
            continue
        try:
            result.append(enrich_korea(doc, client))
        except (ValueError, httpx.HTTPError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            result.append(doc)
            issues.append('일부 브리핑 본문을 확인하지 못해 원문 링크만 저장했습니다.')
            if _korea_host_unavailable(exc):
                host_unavailable = True
                issues.append(f'브리핑 본문 연결을 확인하지 못했습니다 ({_source_failure_kind(exc)}).')
    if host_unavailable:
        issues.append('연결 오류로 동일 자료원의 남은 목록·본문 요청을 중단했습니다.')
    return SourceDocuments(result, complete=not issues, coverage_note=' '.join(dict.fromkeys(issues)) or None)


def enrich_dvids(document,client):
    url=trusted_detail_url(document['url'],'www.dvidshub.net','/news/')
    html=fetch_text(url,client);soup=BeautifulSoup(html,'html.parser')
    notice=soup.select_one('.public-domain');body=soup.select_one('.news-body')
    if not notice or 'PUBLIC DOMAIN' not in notice.get_text(' ',strip=True).upper() or not body:
        return document
    for tag in body.select('script,style,figure,img'): tag.decompose()
    text=body.get_text('\n',strip=True)
    if len(text)<80:return document
    rights={'license':'DVIDS-Public-Domain-item','url':url,'terms_url':'https://www.dvidshub.net/about/copyright','checked_at':utcnow(),
            'scope':'article text only; no images/insignia; no endorsement; attribution',
            'permissions':{'fetch':'allowed','llm':'allowed','summary':'allowed','retention':'allowed'}}
    return dict(document,body=text[:24000],rights=rights,permissions=rights['permissions'],rights_verified=True,body_origin='article_html')


def parse_mod_northkorea(text,source):
    soup=BeautifulSoup(text,'html.parser');docs=[]
    for a in soup.select('a[href]'):
        url=urljoin(source['fetch_url'],a['href'])
        m=re.search(r'/j/press/news/(20\d{2})/(\d{2})/(\d{2})[^/]*\.html$',url)
        if not m or urlsplit(url).hostname!='www.mod.go.jp':continue
        date=f'{m[1]}-{m[2]}-{m[3]}T00:00:00+09:00'
        if datetime.fromisoformat(date)<datetime.now(timezone.utc)-timedelta(days=30):continue
        docs.append({'title':a.get_text(' ',strip=True) or '북한 관련 일본 방위성 발표','url':url,'published_at':valid_timestamp(date),'published_precision':'day','body':''})
    if not docs:raise ValueError('최근 북한 관련 발표 목록을 확인하지 못했습니다.')
    return SourceDocuments(docs[:30],complete=len(docs)<=30)


def fetch_source(source, client=None):
    if source.get("permissions", {}).get("fetch") != "allowed":
        raise ValueError("이 자료원은 수집 권한 확인이 필요합니다.")
    owned=client is None
    client=client or httpx.Client(timeout=20,follow_redirects=False,headers={'User-Agent':USER_AGENT})
    try:
        if source['parser']=='korea_history': return fetch_korea_history(source,client)
        if source['parser']=='korea_briefings': return fetch_korea_briefings(source,client)
        if source['parser']=='us_travel_advisory':
            from .us_travel import fetch_us_travel
            return SourceDocuments([], observation=fetch_us_travel(source,client=client))
        if source['parser']=='korean_alerts':
            from .korean_alerts import fetch_korean_alerts
            observation=fetch_korean_alerts(source,client=client)
            return SourceDocuments([], complete=observation.get('complete',False), coverage_note=observation.get('note'), observation=observation)
        text = fetch_text(source["fetch_url"], client=client)
        parser = {"govuk": parse_govuk, "korea": parse_korea, "jso": parse_jso, "rss_metadata": parse_rss_metadata,'mod_northkorea':parse_mod_northkorea}.get(source["parser"])
        if parser is None:raise ValueError("알 수 없는 자료원 파서")
        documents=parser(text, source)
        if source['parser']=='govuk' and source.get('monitor'):
            details=json.loads(text)['details']
            observation={'kind':'travel_advice','label':'한국 여행정보','url':source['url'],
                         'published_at':documents[0].get('published_at'),
                         'published_precision':documents[0].get('published_precision','unknown'),
                         'note':BeautifulSoup(str(details.get('change_description','')), 'html.parser').get_text(' ',strip=True)[:500]}
            return SourceDocuments(documents,observation=observation)
        if source.get('per_document_rights')=='dvids_public_domain':
            if source.get('lookback_days'):
                cutoff=datetime.now(timezone.utc)-timedelta(days=int(source['lookback_days']))
                documents=SourceDocuments([d for d in documents if not d.get('published_at') or datetime.fromisoformat(d['published_at'].replace('Z','+00:00'))>=cutoff],complete=getattr(documents,'complete',True))
            failures=0;result=[]
            for doc in documents:
                try:result.append(enrich_dvids(doc,client))
                except (ValueError,httpx.HTTPError):failures+=1;result.append(doc)
            return SourceDocuments(result,complete=getattr(documents,'complete',True) and not failures)
        return documents
    finally:
        if owned:client.close()
