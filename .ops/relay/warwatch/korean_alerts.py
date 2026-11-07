"""Read the public, nationwide 국민안전24 SMS listing, not an active-alert feed.

The HTML route and its date/page controls were checked on 2026-11-03.
No authenticated API, LLM, or claim about the absence of an active alert is used.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from urllib.parse import urlencode, urlsplit

import httpx
from bs4 import BeautifulSoup


LIST_URL = "https://www.safekorea.go.kr/safekorea-kor/ctim/cmsg/calamitySms.do?menuSn=34"
DETAIL_URL = "https://www.safekorea.go.kr/safekorea-kor/ctim/cmsg/calamitySmsView.do"
KST = timezone(timedelta(hours=9))
PAGE_SIZE = 10
RELATED = re.compile(
    r"민방공|민방위|공습|경계\s*경보|핵\s*경보|화생방\s*경보|"
    r"북한(?!산)|북측|미사일|포격|대남|오물\s*풍선|전시\s*상황|적의\s*공격"
)


def _clean(value):
    return re.sub(r"\s+", " ", value).strip()


def _category(text):
    # An explicit 'not a drill' must never be displayed as an exercise.
    real = re.search(r"(?:훈련|연습|모의)(?:이|가)?\s*아(?:닙|닌|니)|훈련\s*아님", text)
    if re.search(r"오발령|잘못\s*발령|오류\s*발령|정정", text):
        return "correction"
    if not real and re.search(r"훈련|연습|모의", text):
        return "exercise"
    if re.search(r"(?:경보|공습|대피|민방공).{0,16}해제", text):
        return "released"
    if re.search(r"민방공|공습\s*경보|경계\s*경보|핵\s*경보|화생방\s*경보", text):
        return "civil_defense"
    return "related_notice"


def parse_korean_alert_page(text, *, start_date, end_date, page):
    """Fail closed on changed, blocked, ignored-filter, or malformed HTML."""
    soup = BeautifulSoup(text, "html.parser")
    for field, expected in (("startDate", start_date), ("endDate", end_date), ("currentPage", str(page))):
        node = soup.find(id=field)
        if node is None or node.get("value") != expected:
            raise ValueError("국민안전24 조회 기간 또는 페이지가 요청과 다릅니다.")
    count_node = soup.select_one(".board-count")
    count_match = re.fullmatch(r"전체\s*([\d,]+)\s*건", _clean(count_node.get_text(" "))) if count_node else None
    tables = [table for table in soup.find_all("table")
              if table.caption and "재난문자 목록" in table.caption.get_text()]
    if count_match is None or len(tables) != 1 or tables[0].tbody is None:
        raise ValueError("국민안전24 재난문자 목록 구조를 확인하지 못했습니다.")
    total = int(count_match[1].replace(",", ""))
    rows = []
    for row in tables[0].tbody.find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        if total == 0 and len(cells) == 1 and re.search(r"없습니다|없음", cells[0].get_text()):
            continue
        if len(cells) != 2:
            raise ValueError("국민안전24 문자 행이 예상 형식과 다릅니다.")
        link = cells[1].find("a", href=True)
        ident = re.fullmatch(r"javascript:onSubmit\('(\d+)'\);?", link["href"]) if link else None
        body = _clean(link.get_text(" ")) if link else ""
        metadata = _clean(cells[1].get_text(" "))
        stamp = re.search(r"발송일시\s*:\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", metadata)
        severity = re.search(r"긴급단계\s*:\s*([^ㆍ]+)\s*ㆍ\s*송출지역", metadata)
        region = re.search(r"송출지역\s*:\s*(.+)$", metadata)
        if not ident or not body or not stamp or not severity or not region:
            raise ValueError("국민안전24 문자의 번호·발송 시각·긴급단계·지역을 확인하지 못했습니다.")
        issued = datetime.strptime(stamp[1], "%Y/%m/%d %H:%M:%S").replace(tzinfo=KST)
        if not start_date <= issued.date().isoformat() <= end_date:
            raise ValueError("국민안전24에 요청 기간 밖의 문자가 포함되어 있습니다.")
        rows.append({"id": ident[1], "text": body, "issued_at": issued.isoformat(),
                     "region": _clean(region[1]), "severity": _clean(severity[1]),
                     "disaster_type": _clean(cells[0].get_text(" ")),
                     "url": DETAIL_URL + "?" + urlencode({"menuSn": "34", "smsTrsmSn": ident[1]})})
    expected = min(PAGE_SIZE, max(0, total - (page - 1) * PAGE_SIZE))
    if len(rows) != expected or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("국민안전24 표시 건수와 실제 문자 행이 일치하지 않습니다.")
    return total, rows


def fetch_korean_alerts(source, client=None, *, now=None):
    """Return a bounded 24h observation; partial coverage remains explicit.

    total_count counts all retrieved messages inside the exact 24h window.
    listing_count is the site's count over the two KST calendar dates. Related
    items are excerpts of issued records, never assertions of a current state.
    """
    configured = urlsplit(source.get("fetch_url", LIST_URL))
    approved = urlsplit(LIST_URL)
    if (configured.scheme, configured.netloc, configured.path) != (approved.scheme, approved.netloc, approved.path):
        raise ValueError("국민안전24 공식 재난문자 목록 주소만 수집합니다.")
    end = now or datetime.now(KST)
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("국민안전24 조회 시각에 시간대가 필요합니다.")
    end = end.astimezone(KST).replace(microsecond=0)
    start = end - timedelta(hours=24)
    start_date, end_date = start.date().isoformat(), end.date().isoformat()
    max_pages = max(1, min(10, int(source.get("max_pages", 10))))
    # Reuse the project's bounded HTTPS fetch/retry behavior without a cycle.
    from .sources import USER_AGENT, fetch_text
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT})
    rows, ids = [], set()
    listing_count = None
    complete, changed, pages_fetched = True, False, 0
    try:
        for page in range(1, max_pages + 1):
            url = LIST_URL + "&" + urlencode({"startDate": start_date, "endDate": end_date,
                                               "currentPage": page, "readYn": "Y"})
            total, page_rows = parse_korean_alert_page(fetch_text(url, client),
                start_date=start_date, end_date=end_date, page=page)
            pages_fetched += 1
            if listing_count is None:
                listing_count = total
            elif total != listing_count:
                changed = True
                complete = False
            for row in page_rows:
                if row["id"] in ids:
                    complete = False
                    changed = True
                    continue
                ids.add(row["id"])
                issued = datetime.fromisoformat(row["issued_at"])
                if issued > end:
                    # A new SMS may arrive during pagination. It is excluded
                    # from the frozen time window, but coverage is not claimed.
                    complete = False
                    changed = True
                elif issued >= start:
                    rows.append(row)
            if page >= max(1, math.ceil(total / PAGE_SIZE)):
                break
        if len(ids) != listing_count:
            complete = False
    finally:
        if owned:
            client.close()
    items = []
    for row in rows:
        if row["disaster_type"] == "민방공" or RELATED.search(row["text"]):
            item = dict(row)
            item["category"] = _category(row["disaster_type"] + " " + row["text"])
            # Keep a short attributed excerpt and a direct official detail link.
            item["excerpt_truncated"] = len(item["text"]) > 180
            item["text"] = item["text"][:180] + ("…" if item["excerpt_truncated"] else "")
            items.append(item)
    items.sort(key=lambda item: (item["issued_at"], item["id"]), reverse=True)
    note = "전국 재난문자 발송 기록을 최근 24시간 기준으로 조회합니다. 수집된 0건은 활성 경보가 없다는 뜻이 아닙니다. 휴대전화 재난문자와 발령기관 안내를 우선하세요."
    if not complete:
        note = ("조회 도중 목록이 변경되어 일부 문자가 누락될 수 있습니다. " if changed else
                f"최대 {max_pages}쪽만 조회한 부분 수집 결과입니다. ") + note
    return {"kind": "disaster_messages", "url": LIST_URL,
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "total_count": len(rows), "matched_count": len(items),
            "listing_count": listing_count, "pages_fetched": pages_fetched,
            "complete": complete, "note": note, "items": items,
            "scope": "국민안전24 전국 재난문자 발송 기록 · 최근 24시간",
            "match_method": "공식 민방공 분류 또는 민방위·공습·북한·미사일 등 관련 문구"}
