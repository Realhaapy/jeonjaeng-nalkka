"""Publish metadata for official Korean civil-defence SMS notices. Python 3.10+."""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlencode

HOST = "www.safekorea.go.kr"
LIST_PATH = "/safekorea-kor/ctim/cmsg/calamitySms.do"
DETAIL_URL = f"https://{HOST}/safekorea-kor/ctim/cmsg/calamitySmsView.do?menuSn=34&smsTrsmSn="
FILTER = {"menuSn": "34", "cOcrcType": "DST400", "dsstrSeId": "40", "readYn": "Y"}
SOURCE_URL = f"https://{HOST}{LIST_PATH}?{urlencode(FILTER)}"
KST = timezone(timedelta(hours=9))
VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())


@dataclass
class Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self):
        return " ".join(" ".join(x.text() if isinstance(x, Element) else x
                                 for x in self.children).split())

    def find(self, tag):
        for x in self.children:
            if isinstance(x, Element):
                if x.tag == tag:
                    yield x
                yield from x.find(tag)


class HtmlTree(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.root = Element("")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            if len(self.stack) > 150:
                raise ValueError("Unexpected HTML nesting")
            self.stack.append(node)

    def handle_endtag(self, tag):
        for pos in range(len(self.stack) - 1, 0, -1):
            if self.stack[pos].tag == tag:
                del self.stack[pos:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def status_of(body, severity):
    body = re.sub(r"(?:훈련|연습|모의)(?:이|가)?\s*아(?:닙니다|님|닌|니라)", "", body)
    if re.search(r"훈련|연습|모의|시험\s*발송", body):
        return "exercise"
    if re.search(r"(?:해제|발령|오발송|정정)(?:이|가|은|는)?\s*(?:아님|아닙|아니|않)"
                 r"|(?:해제|발령)(?:되지|하지)\s*않", body):
        return "notice"
    if re.search(r"오발령|오발송|잘못\s*(?:발송|발령)|정정", body):
        return "correction"
    if re.search(r"예정|(?:발령|해제)\s*(?:시(?:에는|에|\s)|여부|가능성|될|할)|미해제", body):
        return "notice"
    ends = re.search(r"(?:경보|대피명령|대피령).{0,15}해제", body)
    starts = re.search(r"(?:공습|경계|민방공|화생방|핵)\s*경보.{0,8}발령", body)
    if ends and starts:
        return "notice"
    if ends:
        return "lifted"
    if starts or (severity == "위급재난" and re.search(r"(?:공습|경계|민방공)\s*경보[,.\s]", body)
                  and "대피" in body):
        return "issued"
    return "notice"


def effective_time(body, sent, status):
    if status not in ("issued", "lifted"):
        return None
    times = list(re.finditer(r"(?<![\d:])([01]\d|2[0-3]):([0-5]\d)(?![:\d])", body))
    if len(times) != 1:
        return None
    t = times[0]
    indicator = r"(?:경보|대피령|대피명령).{0,15}해제" if status == "lifted" else r"(?:경계|공습|민방공|핵|화생방)\s*경보"
    if not re.search(indicator, body[t.end():t.end() + 95]):
        return None
    candidate = sent.replace(hour=int(t[1]), minute=int(t[2]), second=0, microsecond=0)
    if candidate > sent:
        candidate -= timedelta(days=1)
    return candidate.isoformat() if sent - candidate <= timedelta(minutes=30) else None


def parse_row(cells, start_date, end_date):
    if len(cells) != 2 or cells[0].text() != "민방공":
        raise ValueError("Unexpected disaster type or row structure")
    anchors = list(cells[1].find("a"))
    paras = list(cells[1].find("p"))
    if len(anchors) != 1 or len(paras) != 1:
        raise ValueError("Missing SMS detail link or metadata")
    serial = re.fullmatch(r"javascript:onSubmit\('(\d{1,16})'\);?", anchors[0].attrs.get("href", ""))
    metadata = re.fullmatch(
        r"ㆍ?\s*발송일시\s*:\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s*ㆍ\s*"
        r"긴급단계\s*:\s*([^ㆍ]+?)\s*ㆍ\s*송출지역\s*:\s*(.{1,2000})", paras[0].text())
    body = anchors[0].text()
    if not serial or not metadata or not body:
        raise ValueError("Unrecognized SMS metadata")
    sent = datetime.strptime(metadata[1], "%Y/%m/%d %H:%M:%S").replace(tzinfo=KST)
    if not start_date <= sent.date().isoformat() <= end_date:
        raise ValueError("SMS outside requested calendar dates")
    agency = re.search(r"\[([^\[\]]{1,80})\]\s*$", body)
    issuer = agency[1].strip() if agency and agency[1].strip() not in {"훈련", "실제", "연습"} else ""
    status = status_of(body, metadata[2])
    item = dict(id=serial[1], issued_at=sent.isoformat(), region=metadata[3], status=status,
                issuer=issuer, url=DETAIL_URL + serial[1])
    effective = effective_time(body, sent, status)
    if effective:
        item["effective_at"] = effective
    return item


def parse_page(html, start, end, page):
    doc = HtmlTree(html).root
    controls = {el.attrs.get("id"): el.attrs.get("value") for el in doc.find("input")}
    if any(controls.get(key) != value for key, value in
           (("startDate", start), ("endDate", end), ("currentPage", str(page)))):
        raise ValueError("The source did not confirm the requested date and page")
    for name, code in (("cOcrcTypeVal", "DST400"), ("dsstrSeIdVal", "40")):
        if not re.search(r'\blet\s+' + name + r'\s*=\s*["\']' + code + r'["\']', html):
            raise ValueError("The source did not confirm the civil-defence filter")
    counts = [e for e in doc.find("div") if "board-count" in e.attrs.get("class", "").split()]
    tables = [t for t in doc.find("table") if any("재난문자 목록" in c.text() for c in t.find("caption"))]
    if len(counts) != 1 or len(tables) != 1:
        raise ValueError("Unrecognized government SMS listing")
    count = re.fullmatch(r"전체\s*([\d,]+)\s*건", counts[0].text())
    if not count:
        raise ValueError("Missing government list count")
    total = int(count[1].replace(",", ""))
    bodies = list(tables[0].find("tbody"))
    if len(bodies) != 1:
        raise ValueError("Missing message table")
    items = []
    for tr in bodies[0].find("tr"):
        cells = list(tr.find("td"))
        if not total and len(cells) == 1 and re.search(r"없습니다|없음", cells[0].text()):
            continue
        items.append(parse_row(cells, start, end))
    if len(items) != min(10, max(0, total - 10 * (page - 1))):
        raise ValueError("Government message count differs from table")
    if len({i["id"] for i in items}) != len(items):
        raise ValueError("Duplicate SMS identifier on the same page")
    return total, items


def fetch_page(start, end, page):
    params = dict(FILTER, startDate=start, endDate=end, currentPage=page)
    connection = http.client.HTTPSConnection(HOST, timeout=12)
    try:
        connection.request("GET", LIST_PATH + "?" + urlencode(params),
                           headers={"User-Agent": "Warwatch-Civil-Notices/1.0 (+https://war.pebbleroom.kr/)",
                                    "Accept": "text/html", "Cache-Control": "no-cache"})
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Type", "").split(";")[0] != "text/html":
            raise ValueError(f"Official listing unavailable (HTTP {response.status})")
        content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise ValueError("Unexpected listing size")
        return content.decode("utf-8")
    finally:
        connection.close()


def collect(now, fetch=fetch_page):
    if now.tzinfo is None:
        raise ValueError("A timezone is required")
    end = now.astimezone(KST).replace(microsecond=0)
    start = end - timedelta(hours=24)
    start_date, end_date = start.date().isoformat(), end.date().isoformat()
    items = {}
    complete = True
    first_total = None
    for page in range(1, 11):
        total, rows = parse_page(fetch(start_date, end_date, page), start_date, end_date, page)
        if first_total is None:
            first_total = total
        for item in rows:
            if item["id"] in items or datetime.fromisoformat(item["issued_at"]) > end:
                complete = False
            items[item["id"]] = item
        if total != first_total:
            complete = False
        if page * 10 >= total:
            break
    complete = complete and len(items) == first_total
    observed = [item for item in items.values() if start <= datetime.fromisoformat(item["issued_at"]) <= end]
    observed.sort(key=lambda item: (item["issued_at"], int(item["id"])), reverse=True)
    return dict(schema=1, checked_at=end.isoformat(), window_start=start.isoformat(), complete=complete,
                source_url=SOURCE_URL, items=observed)


def write_snapshot(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    try:
        with handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(handle.name, path)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = collect(datetime.now(KST))
        write_snapshot(args.output, data)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        print(f"Civil notice refresh failed: {exc}", file=sys.stderr)
        return 1
    print(f"{len(data['items'])} civil notice records, complete={data['complete']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
