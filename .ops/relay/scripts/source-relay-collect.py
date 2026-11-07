"""Collect only the three approved official sources into an encrypted relay.

No database, API keys, model calls, publication or plaintext output files.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from warwatch.relay_crypto import REPOSITORY, SOURCE_IDS, encrypt_payload, load_public_key, source_fingerprint
from warwatch.sources import fetch_source, load_config
from warwatch.store import canonical_url, public_observation

APPROVED = {
    'korea-briefing': ('korea_history', 'www.korea.kr', '/briefing/pressReleaseList.do'),
    'korea-ministry-briefings': ('korea_briefings', 'www.korea.kr', '/briefing/policyBriefingList.do'),
    'korea-disaster-messages': ('korean_alerts', 'www.safekorea.go.kr', '/safekorea-kor/ctim/cmsg/calamitySms.do'),
}


def _stamp(clock):
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Relay clock must provide an aware UTC timestamp')
    return now.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _safe_error(exc):
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(getattr(current, 'response', None), 'status_code', None)
        if type(status) is int:
            return f'자료원 응답 오류 (HTTP {status}).'
        if current.__cause__ is None:
            return f'자료원 수집 실패 ({type(current).__name__}).'
        current = current.__cause__
    return '자료원 수집 실패.'


def _document(source, document):
    """Mirror the store's KOGL-1 retention gate before any encrypted retention."""
    if not isinstance(document, dict):
        raise ValueError('Invalid relay source document')
    fields = {'title', 'url', 'published_at', 'published_precision', 'agency_context', 'revision_hint', 'body_error'}
    result = {key: document[key] for key in fields if key in document}
    rights = document.get('rights', {})
    allowed = (source.get('per_document_rights') == 'kogl_text'
               and document.get('rights_verified') is True
               and isinstance(rights, dict) and rights.get('license') == 'KOGL-1'
               and canonical_url(rights.get('url', '')) == canonical_url(document.get('url', ''))
               and all(rights.get('permissions', {}).get(key) == 'allowed'
                       for key in ('fetch', 'llm', 'summary', 'retention')))
    result['body'] = str(document.get('body', ''))[:24000] if allowed else ''
    result['permissions'] = dict(rights['permissions'] if allowed else source.get('permissions', {}))
    result['rights_verified'] = bool(allowed)
    if allowed:
        result['rights'] = {key: rights[key] for key in ('license', 'url', 'terms_url', 'checked_at', 'scope', 'permissions') if key in rights}
        result['body_origin'] = str(document.get('body_origin', ''))
    # listing_excerpt and non-authorized bodies/rights never enter the packet.
    return result


def collect_payload(config, *, fetcher=None, clock=None):
    fetcher = fetcher or fetch_source
    clock = clock or (lambda: datetime.now(timezone.utc))
    entries = config.get('sources', [])
    ids = [item.get('id') for item in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate relay source configuration')
    sources = {item['id']: item for item in entries if item.get('id') in SOURCE_IDS}
    if set(sources) != set(SOURCE_IDS):
        raise ValueError('Relay requires all three approved sources')
    for ident, source in sources.items():
        parser, host, path = APPROVED[ident]
        url = urlsplit(source.get('fetch_url', ''))
        if (not source.get('enabled') or source.get('permissions', {}).get('fetch') != 'allowed'
                or source.get('parser') != parser or url.scheme != 'https'
                or url.netloc != host or url.path != path):
            raise ValueError('Relay source configuration is not approved')
    results = []
    for ident in SOURCE_IDS:
        source = sources[ident]
        result = {'id': ident, 'config_fingerprint': source_fingerprint(source)}
        try:
            docs = fetcher(source)
            checked_at = _stamp(clock)  # Actual fetch completion, never download/import time.
            documents = [_document(source, doc) for doc in docs]
            maximum = {'korea-briefing': 60, 'korea-ministry-briefings': 30, 'korea-disaster-messages': 0}[ident]
            if len(documents) > maximum:
                raise ValueError('Relay source exceeds document limit')
            observation = getattr(docs, 'observation', None)
            if source.get('monitor') and observation is None:
                raise ValueError('Required source observation is missing')
            if observation is not None:
                observation = public_observation(observation)
            result.update(checked_at=checked_at, status='ok', error=None,
                          complete=bool(getattr(docs, 'complete', True)),
                          coverage_note=getattr(docs, 'coverage_note', None), documents=documents,
                          observation=observation, listing_count=getattr(docs, 'listing_count', None),
                          total_count=getattr(docs, 'total_count', None), has_more=bool(getattr(docs, 'has_more', False)))
        except Exception as exc:
            result.update(checked_at=_stamp(clock), status='failed', error=_safe_error(exc), complete=False,
                          coverage_note=None, documents=[], observation=None, listing_count=None, total_count=None, has_more=False)
        results.append(result)
    return {'schema_version': 1, 'repository': REPOSITORY, 'generated_at': _stamp(clock), 'sources': results}


def write_envelope(path, encrypted):
    """Atomic encrypted-only output; no plaintext temporary file exists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--public-key', '--recipient-public-key', dest='public_key', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if Path(args.output).resolve() in {Path(args.config).resolve(), Path(args.public_key).resolve()}:
            raise ValueError('Relay output overlaps an input')
        recipient = load_public_key(args.public_key)
        config = load_config(args.config)
        payload = collect_payload(config)
        encrypted = encrypt_payload(payload, recipient)
        write_envelope(args.output, encrypted)
    except Exception:
        print('Relay collection or encryption failed; no plaintext was written.', file=sys.stderr)
        return 1
    print(f'Encrypted relay written: bytes={len(encrypted)} sha256={hashlib.sha256(encrypted).hexdigest()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
