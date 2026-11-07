"""Local audit store. Only explicit public allowlists leave this module."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sqlite3
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


class BudgetExceededError(ValueError):
    """A temporary analysis pause, not a failure of the source document."""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_url(value):
    p = urlsplit(str(value))
    if p.scheme not in {"http", "https"} or not p.netloc or p.username or p.password:
        raise ValueError("공개 출처는 http/https URL이어야 합니다.")
    host = (p.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        raise ValueError("로컬 주소는 공개 출처로 사용할 수 없습니다.")
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"})
    if host.endswith("korea.kr") and any(k == "newsId" for k, _ in query):
        query = [(k, v) for k, v in query if k == "newsId"]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(query), ""))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def valid_timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("시각은 시간대가 있는 ISO 문자열 또는 null이어야 합니다.")
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("시각에 시간대를 포함하세요.")
    return d.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def public_observation(value):
    """Validate official structured facts and project an explicit public allowlist."""
    if not isinstance(value, dict):
        raise ValueError("공식 관측값은 객체여야 합니다.")
    kind = value.get("kind")
    if kind not in {"travel_advisory", "travel_advice", "disaster_messages"}:
        raise ValueError("지원하지 않는 공식 관측 유형입니다.")
    result = {"kind": kind, "url": canonical_url(value.get("url", ""))}
    for key, limit in (("label", 500), ("note", 4000)):
        if key in value:
            if not isinstance(value[key], str) or len(value[key]) > limit:
                raise ValueError("공식 관측 설명 형식 또는 길이가 잘못됐습니다.")
            result[key] = value[key].strip()
    if "level" in value:
        level = value["level"]
        if type(level) is not int or not 1 <= level <= 4:
            raise ValueError("여행경보 단계는 1~4 정수여야 합니다.")
        result["level"] = level
    if kind == "travel_advisory" and "level" not in result:
        raise ValueError("공식 여행경보 단계를 확인하지 못했습니다.")
    if kind == "travel_advisory" and not result.get("label"):
        raise ValueError("공식 여행경보 단계 설명을 확인하지 못했습니다.")
    for key in ("published_at", "window_start", "window_end"):
        if key in value:
            result[key] = valid_timestamp(value[key])
    if "published_precision" in value:
        precision = value["published_precision"]
        if precision not in {"day", "minute", "unknown"}:
            raise ValueError("공식 관측 날짜 정밀도가 잘못됐습니다.")
        result["published_precision"] = precision if result.get("published_at") else "unknown"
    for key in ("total_count", "matched_count"):
        if key in value:
            count = value[key]
            if type(count) is not int or count < 0:
                raise ValueError("공식 자료 건수는 음수가 아닌 정수여야 합니다.")
            result[key] = count
    if "complete" in value:
        if type(value["complete"]) is not bool:
            raise ValueError("조회 범위 완료 상태가 잘못됐습니다.")
        result["complete"] = value["complete"]
    if result.get("window_start") and result.get("window_end") and result["window_start"] > result["window_end"]:
        raise ValueError("공식 자료 조회 기간이 뒤집혔습니다.")
    if "total_count" in result and result.get("matched_count", 0) > result["total_count"]:
        raise ValueError("선별 건수가 전체 조회 건수를 넘습니다.")
    if "items" in value:
        if not isinstance(value["items"], list) or len(value["items"]) > 200:
            raise ValueError("공식 자료 항목 목록 형식 또는 상한이 잘못됐습니다.")
        result["items"] = []
        for item in value["items"]:
            if not isinstance(item, dict):
                raise ValueError("공식 자료 항목은 객체여야 합니다.")
            record = {}
            for key, limit in (("id", 200), ("title", 500), ("text", 8000), ("region", 1000), ("category", 100), ("disaster_type", 100), ("severity", 100)):
                if key in item:
                    if not isinstance(item[key], str) or len(item[key]) > limit:
                        raise ValueError("공식 자료 항목의 문자열 형식 또는 길이가 잘못됐습니다.")
                    record[key] = item[key].strip()
            if "issued_at" in item:
                record["issued_at"] = valid_timestamp(item["issued_at"])
            if "url" in item:
                record["url"] = canonical_url(item["url"])
            result["items"].append(record)
        if "matched_count" in result and len(result["items"]) > result["matched_count"]:
            raise ValueError("공개 항목 수가 선별 건수를 넘습니다.")
    if kind == "disaster_messages":
        required = {"total_count", "matched_count", "window_start", "window_end", "complete", "items"}
        if not required.issubset(result) or any(result[key] is None for key in required):
            raise ValueError("재난문자 조회 기간·건수·완료 상태·목록이 필요합니다.")
        for item in result["items"]:
            if any(not item.get(key) for key in ("id", "text", "issued_at", "region", "category", "url")):
                raise ValueError("재난문자의 식별자·본문·발송시각·지역·유형·원문이 필요합니다.")
    return result


def observation_semantics(records):
    """Polling clocks alone do not create a different frozen briefing."""
    return [{key: record.get(key) for key in ("source_id", "status", "observation")}
            for record in records]


class Store:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_checks(id INTEGER PRIMARY KEY, source_id TEXT NOT NULL,
            checked_at TEXT NOT NULL, result TEXT NOT NULL, coverage_at TEXT, error TEXT,
            document_count INTEGER NOT NULL DEFAULT 0, scope_complete INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS source_observations(source_id TEXT PRIMARY KEY,
            observed_at TEXT NOT NULL, value_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
            url TEXT NOT NULL, title TEXT NOT NULL, published_at TEXT, first_seen_at TEXT NOT NULL,
            content_hash TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL DEFAULT '',
            permissions TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', candidate TEXT,
            analysis_error TEXT, UNIQUE(url, content_hash));
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, public_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS event_revisions(event_id TEXT NOT NULL, revision INTEGER NOT NULL,
            public_json TEXT NOT NULL, PRIMARY KEY(event_id, revision));
        CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
            text TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(claim_id TEXT NOT NULL, source_url TEXT NOT NULL,
            origin_group TEXT, PRIMARY KEY(claim_id, source_url));
        CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
            at TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, document_id TEXT, at TEXT NOT NULL,
            decision TEXT NOT NULL, private_note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, report TEXT);
        CREATE TABLE IF NOT EXISTS budget(id TEXT PRIMARY KEY, month TEXT NOT NULL, reserved REAL NOT NULL,
            actual REAL, status TEXT NOT NULL, created_at TEXT NOT NULL, model TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY, generated_at TEXT NOT NULL,
            content_hash TEXT NOT NULL, public_json TEXT NOT NULL);
        """)
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(documents)")}
        if "published_precision" not in columns:
            self.db.execute("ALTER TABLE documents ADD COLUMN published_precision TEXT NOT NULL DEFAULT 'unknown'")
        for name,default in [('rights_json','{}'),('body_origin',''),('body_error','')]:
            if name not in columns:
                self.db.execute(f"ALTER TABLE documents ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
        self.db.commit()

    def close(self):
        self.db.close()

    def configure(self, config):
        with self.db:
            incoming = {s["id"] for s in config["sources"]}
            for previous in self.source_configs():
                if previous["id"] not in incoming:
                    previous["enabled"] = False
                    self.db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(previous, ensure_ascii=False), previous["id"]))
            for source in config["sources"]:
                self.db.execute("INSERT INTO sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                                (source["id"], json.dumps(source, ensure_ascii=False)))
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('budget_usd',?)", (str(config.get("budget_usd", 5)),))

    def source_configs(self):
        return [json.loads(r["config"]) for r in self.db.execute("SELECT config FROM sources ORDER BY rowid")]

    def add_document(self, source, document):
        url = canonical_url(document["url"])
        title = str(document["title"]).strip()[:500]
        if not title:
            raise ValueError("문서 제목이 없습니다.")
        permissions = dict(source['permissions'])
        rights = document.get('rights',{})
        # Affiliate-supported publication does not unlock noncommercial-only text.
        allowed_licenses={'kogl_text':{'KOGL-1'},'dvids_public_domain':{'DVIDS-Public-Domain-item'}}
        if document.get('rights_verified') and rights.get('license') in allowed_licenses.get(source.get('per_document_rights'),set()) and canonical_url(rights.get('url','')) == url:
            permissions = dict(rights['permissions'])
        body = str(document.get("body", ""))[:24000]
        if permissions.get("retention") != "allowed":
            body = ""
        published_at = valid_timestamp(document.get("published_at"))
        published_precision = document.get("published_precision", "minute" if published_at else "unknown")
        if published_precision not in {"minute", "day", "unknown"} or not published_at:
            published_precision = "unknown"
        h = digest({"title": title, "body": body, "published_at": published_at,
                    "revision_hint": document.get("revision_hint")})
        existing = self.db.execute("SELECT id FROM documents WHERE url=? AND content_hash=?", (url, h)).fetchone()
        if existing:
            with self.db:
                self.db.execute("UPDATE documents SET published_precision=? WHERE id=?", (published_precision, existing["id"]))
            return existing["id"], False
        revision = self.db.execute("SELECT coalesce(max(revision),0)+1 FROM documents WHERE url=?", (url,)).fetchone()[0]
        doc_id = "doc-" + digest([url, h])[:20]
        with self.db:
            self.db.execute("UPDATE documents SET status='superseded' WHERE url=? AND status IN ('pending','held')", (url,))
            self.db.execute("INSERT INTO documents(id,source_id,url,title,published_at,first_seen_at,content_hash,revision,body,permissions,published_precision) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (doc_id, source["id"], url, title, published_at, utcnow(), h, revision, body,
                             json.dumps(permissions), published_precision))
            self.db.execute('UPDATE documents SET rights_json=?,body_origin=?,body_error=? WHERE id=?',
                (json.dumps(rights,ensure_ascii=False),str(document.get('body_origin','')),str(document.get('body_error','')),doc_id))
        return doc_id, True

    def save_observation(self, source_id, observed_at, value, *, commit=True):
        projected = public_observation(value)
        stamp = valid_timestamp(observed_at)
        if stamp is None:
            raise ValueError("공식 관측 성공 시각이 필요합니다.")
        params = (source_id, stamp, json.dumps(projected, ensure_ascii=False))
        query = "INSERT INTO source_observations VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET observed_at=excluded.observed_at,value_json=excluded.value_json"
        if commit:
            with self.db:
                self.db.execute(query, params)
        else:
            self.db.execute(query, params)

    def add_check(self, source_id, checked_at, result, coverage_at=None, error=None, document_count=0, complete=False, observation=None):
        if observation is not None and result != "ok":
            raise ValueError("실패한 수집은 마지막 정상 관측값을 바꿀 수 없습니다.")
        with self.db:
            if observation is not None:
                self.save_observation(source_id, checked_at, observation, commit=False)
            self.db.execute("INSERT INTO source_checks(source_id,checked_at,result,coverage_at,error,document_count,scope_complete) VALUES(?,?,?,?,?,?,?)",
                            (source_id, checked_at, result, coverage_at, error, document_count, int(complete)))

    def budget_total(self, month=None):
        month = month or utcnow()[:7]
        return float(self.db.execute("SELECT coalesce(sum(CASE WHEN actual IS NULL THEN reserved ELSE actual END),0) FROM budget WHERE month=? AND status!='cancelled'", (month,)).fetchone()[0])

    def reserve_budget(self, amount, model, limit=5):
        if amount < 0:
            raise ValueError("예상 호출 비용은 음수일 수 없습니다.")
        if amount > limit:
            raise BudgetExceededError("예상 호출 비용이 예산을 초과합니다.")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            stamp = utcnow()
            month = stamp[:7]
            if self.budget_total(month) + amount > limit:
                raise BudgetExceededError("월 API 예산에 도달해 분석을 보류합니다.")
            rid = "usage-" + uuid4().hex[:20]
            self.db.execute("INSERT INTO budget VALUES(?,?,?,?,?,?,?)", (rid, month, amount, None, "reserved", stamp, model))
            self.db.commit()
            return rid
        except Exception:
            self.db.rollback()
            raise

    def finish_budget(self, rid, actual=None, status="used"):
        # Unknown failure usage keeps the reservation; it is not incorrectly refunded.
        with self.db:
            self.db.execute("UPDATE budget SET actual=?,status=? WHERE id=?", (actual, status, rid))

    def view(self):
        now = utcnow()
        configs = self.source_configs()
        sources = []
        official_observations = []
        coverages = []
        successful_checks = []
        pending = self.db.execute("SELECT count(*) FROM documents WHERE status IN ('pending','held')").fetchone()[0]
        ok = 0
        required = 0
        all_scopes_complete = True
        scope_limited = 0
        for source in configs:
            latest = self.db.execute("SELECT * FROM source_checks WHERE source_id=? ORDER BY id DESC LIMIT 1", (source["id"],)).fetchone()
            successful = self.db.execute("SELECT * FROM source_checks WHERE source_id=? AND result='ok' ORDER BY id DESC LIMIT 1", (source["id"],)).fetchone()
            good = self.db.execute("SELECT * FROM source_checks WHERE source_id=? AND result='ok' AND scope_complete=1 ORDER BY id DESC LIMIT 1", (source["id"],)).fetchone()
            active = source.get("enabled", True)
            state = "disabled" if not active else (latest["result"] if latest else "never")
            if active:
                required += 1
                if state == "ok":
                    ok += 1
                    scope_limited += int(not latest["scope_complete"])
                if not latest or not latest["scope_complete"]:
                    all_scopes_complete = False
                coverages.append(good["coverage_at"] if good else None)
                successful_checks.append(successful["checked_at"] if successful else None)
            sources.append({"id": source["id"], "name": source["name"], "url": source["url"],
                            "status": state, "checked_at": latest["checked_at"] if latest else None,
                            "coverage_at": good["coverage_at"] if good else None,
                            "last_success_at": successful["checked_at"] if successful else None,
                            "error": latest["error"] if latest else None,
                            "permission_note": source.get("permission_note", ""),
                            "scope_note": source.get("scope_note", ""),
                            "scope_complete": bool(latest and latest["scope_complete"])})
            if active and source.get("monitor"):
                observation = self.db.execute("SELECT observed_at,value_json FROM source_observations WHERE source_id=?", (source["id"],)).fetchone()
                official_observations.append({"source_id": source["id"], "name": source["name"], "url": source["url"],
                    "status": state, "checked_at": latest["checked_at"] if latest else None,
                    "last_success_at": successful["checked_at"] if successful else None,
                    "observed_at": observation["observed_at"] if observation else None,
                    "observation": json.loads(observation["value_json"]) if observation else None})
        coverage_at = min(coverages) if coverages and all(coverages) else None
        # A successful bounded poll is fresh even when historical coverage is partial.
        # A failed attempt or a rebuild must never advance this collection clock.
        collection_checked_at = min(successful_checks) if successful_checks and all(successful_checks) else None
        coverage_status = "unconfigured" if not required else ("complete" if ok == required and pending == 0 and all_scopes_complete else "partial")
        from .dashboard import chronological_events, latest_dated_event, parsed
        events = chronological_events([json.loads(r["public_json"]) for r in self.db.execute("SELECT public_json FROM events")])
        changes = [dict(r) for r in self.db.execute("SELECT * FROM changes ORDER BY at DESC, rowid DESC LIMIT 100")]
        notices = []
        names = {s["id"]: s["name"] for s in configs}
        private_ids = {s["id"] for s in configs if s.get("discovery_only")}
        excluded = ",".join("?" for _ in private_ids)
        public_clause = f" AND source_id NOT IN ({excluded})" if private_ids else ""
        for row in self.db.execute("SELECT id,title,url,source_id,published_at,published_precision,first_seen_at,status FROM documents WHERE status IN ('pending','held','reviewed')" + public_clause + " ORDER BY first_seen_at DESC, rowid DESC LIMIT 30", tuple(private_ids)):
            notices.append({"id": row["id"], "title": row["title"], "url": row["url"], "source_name": names.get(row["source_id"], row["source_id"]),
                            "published_at": row["published_at"], "published_precision": row["published_precision"], "first_seen_at": row["first_seen_at"], "status": row["status"]})
        latest_event = latest_dated_event(events, parsed(now))
        if latest_event:
            headline = latest_event["title"]
            summary = latest_event["summary"]
        elif pending:
            headline = "새로 수집한 자료를 확인하고 있습니다."
            summary = "아직 승인된 사건 요약은 없습니다. 공개 가능한 원문 링크만 아래에 표시하며, 자료 수가 사건 수나 위험도를 뜻하지 않습니다."
        else:
            headline = "공식 자료부터 차근히 확인합니다."
            summary = "승인된 사건 기록이 아직 없습니다. 자료 부족을 안전하거나 변화가 없다는 의미로 해석하지 않습니다."
        limit_row = self.db.execute("SELECT value FROM settings WHERE key='budget_usd'").fetchone()
        result = {"schema_version": 1, "mode": "live", "title": "전쟁 날까", "generated_at": now,
                  "assessed_at": None, "coverage_as_of": coverage_at,
                  "collection_checked_at": collection_checked_at,
                  "coverage": {"ok": ok, "total": required, "pending": pending, "status": coverage_status, "scope_limited": scope_limited},
                  "sources": sources, "headline": headline, "summary": summary, "events": events,
                  "changes": changes, "notices": notices, "official_observations": official_observations,
                  "official_note": "확인한 자료원만 다룹니다. 여행정보·군사 발표·주민 경보는 서로 다른 제도입니다.",
                  "unknowns": (["새 자료의 사건 해당 여부와 요약을 검토 중입니다."] if pending else []) +
                              (["일부 자료원을 정상 점검하지 못했습니다."] if ok < required else []),
                  "budget": {"spent": round(self.budget_total(), 6), "limit": float(limit_row[0]) if limit_row else 5.0},
                  "method_version": "1.0"}
        # Complete assessment is separate from a collector merely reaching a page.
        if coverage_status == "complete":
            result["assessed_at"] = coverage_at
        meaningful = {k: result[k] for k in ("mode", "headline", "summary", "events", "changes", "notices", "official_note", "unknowns")}
        meaningful["official_observations"] = observation_semantics(official_observations)
        content_hash = digest(meaningful)
        result["snapshot_id"] = "s-" + content_hash[:16]
        release_seed = [content_hash, [(s["id"], s["checked_at"], s["status"]) for s in sources]]
        result["release_id"] = "r-" + digest(release_seed)[:16]
        return result
