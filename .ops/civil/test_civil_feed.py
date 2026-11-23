import io
import json
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import civil_feed as feed


START = "2026-11-23"
END = "2026-11-24"
NOW = datetime.fromisoformat(END + "T08:45:00+09:00")


def row(ident, text, at="2026/11/24 08:01:17", kind="민방공", severity="안전안내"):
    return f"""<tr><td>{kind}</td><td><a href="javascript:onSubmit('{ident}');">{text}</a>
        <p>ㆍ 발송일시 : {at} ㆍ 긴급단계 : {severity} ㆍ 송출지역 : 시험군</p></td></tr>"""


def listing(rows, page=1, count=None):
    total = len(rows) if count is None else count
    return f"""<input id='startDate' value='{START}'><input id='endDate' value='{END}'>
        <input id='currentPage' value='{page}'><div class='board-count'>전체 <span>{total}</span> 건</div>
        <script>let cOcrcTypeVal = "DST400"; let dsstrSeIdVal = "40";</script>
        <table><caption>재난문자 목록</caption><tbody>{''.join(rows)}</tbody></table>"""


class TestCivilFeed(unittest.TestCase):
    def test_minimal_public_fields_and_send_vs_effective_times(self):
        html = listing([row("123", "[행정안전부] 07:59 시험군 경계경보, 건물 내로 대피. [행정안전부]", severity="위급재난")])
        result = feed.collect(NOW, lambda *_: html)
        self.assertTrue(result["complete"])
        self.assertEqual(result["items"], [{
            "id": "123", "issued_at": END + "T08:01:17+09:00", "region": "시험군",
            "status": "issued", "issuer": "행정안전부", "url": feed.DETAIL_URL + "123",
            "effective_at": END + "T07:59:00+09:00"}])

    def test_each_followup_is_preserved(self):
        html = listing([row("1", "08:00 경계경보 발령 [행정안전부]"),
                        row("2", "08:03 경계경보 해제 [행정안전부]", "2026/11/24 08:04:12"),
                        row("3", "(정정) 이전 문자 내용 정정 [경상북도]", "2026/11/24 08:15:35")])
        result = feed.collect(NOW, lambda *_: html)
        self.assertEqual([x["status"] for x in result["items"]], ["correction", "lifted", "issued"])
        self.assertNotIn("effective_at", result["items"][0])

    def test_status_training_negations_uncertainty(self):
        for body, expected in [
                ("[훈련] 경계경보 해제", "exercise"), ("훈련이 아닙니다. 경계경보 발령", "issued"),
                ("공습경보 해제 예정", "notice"), ("이것은 경보 해제가 아닙니다", "notice"),
                ("공습경보가 발령될 수 있습니다", "notice"), ("공습경보 발령이 아닙니다", "notice"),
                ("경계경보가 해제되지 않았습니다", "notice"), ("공습경보 행동요령", "notice")]:
            with self.subTest(body=body):
                self.assertEqual(feed.status_of(body, "위급재난"), expected)

    def test_no_guessing_times(self):
        sent = datetime.fromisoformat(END + "T00:03:00+09:00")
        self.assertEqual(feed.effective_time("23:59 경계경보 해제", sent, "lifted"), START + "T23:59:00+09:00")
        for text in ("23:59 경계경보 해제, 00:01 재공지", "03:00 경계경보 해제", "경계경보 해제, 상담은 00:01"):
            self.assertIsNone(feed.effective_time(text, sent, "lifted"))

    def test_24h_and_complete_empty(self):
        html = listing([row("1", "경계경보 해제 [행정안전부]", "2026/11/23 08:44:59")])
        result = feed.collect(NOW, lambda *_: html)
        self.assertEqual(result["items"], [])
        self.assertTrue(result["complete"])
        self.assertEqual(feed.parse_page(listing(["<tr><td>조회된 자료가 없습니다.</td></tr>"], count=0),
                                         START, END, 1), (0, []))

    def test_no_exposing_unconfirmed_filters_and_bad_pages(self):
        valid = listing([row("123", "민방공 공지 [행정안전부]")])
        for bad in [valid.replace("DST400", "DST200"), valid.replace("민방공</td>", "대설</td>"),
                    valid.replace("2026-11-24", "2026-11-25"), valid.replace("발송일시", "수집시각"),
                    valid.replace("<span>1</span>", "<span>4</span>"), "<html>Temporary error</html>"]:
            with self.subTest():
                with self.assertRaises(ValueError):
                    feed.parse_page(bad, START, END, 1)

    def test_pagination_change_is_partial(self):
        pages = {1: listing([row(str(n), "공습경보 발령") for n in range(1, 11)], count=11),
                 2: listing([row("10", "공습경보 발령"), row("11", "공습경보 발령")], page=2, count=12)}
        result = feed.collect(NOW, lambda _s, _e, p: pages[p])
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["items"]), 11)

    def test_transport_failure_preserves_previous_snapshot(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            feed.write_snapshot(path, {"previous": True})
            with patch("sys.argv", ["civil_feed.py", "--output", str(path)]), \
                 patch.object(feed, "collect", side_effect=OSError("unavailable")), redirect_stderr(io.StringIO()):
                self.assertEqual(feed.main(), 1)
            self.assertEqual(json.loads(path.read_text()), {"previous": True})


if __name__ == "__main__":
    unittest.main()
