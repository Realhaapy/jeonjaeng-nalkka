"""Offline collector, retention gate and authenticated-encryption regressions."""
import base64
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import httpx

from warwatch import relay_crypto as crypto
from warwatch.sources import SourceDocuments

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('relay_collect_test', ROOT / 'scripts/source-relay-collect.py')
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
PRIVATE_TEXT = 'private-source-text-never-public'
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def config():
    return json.loads((ROOT / 'config/sources.json').read_text(encoding='utf-8'))


def keys():
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw, public


def observation():
    return {'kind': 'disaster_messages', 'url': 'https://www.safekorea.go.kr/safekorea-kor/ctim/cmsg/calamitySms.do?menuSn=34',
            'window_start': (NOW - timedelta(days=1)).isoformat(), 'window_end': NOW.isoformat(),
            'total_count': 2, 'matched_count': 0, 'complete': True, 'items': [], 'note': '공식 목록'}


def document(license_id='KOGL-1'):
    url = 'https://www.korea.kr/briefing/pressReleaseView.do?newsId=123'
    permissions = dict.fromkeys(('fetch', 'llm', 'summary', 'retention'), 'allowed')
    return {'title': '군사 공식 발표', 'url': url, 'published_at': NOW.isoformat(), 'published_precision': 'day',
            'agency_context': '국방부', 'body': PRIVATE_TEXT, 'listing_excerpt': 'unlicensed-excerpt',
            'rights_verified': True, 'permissions': permissions, 'body_origin': 'article_html',
            'rights': {'license': license_id, 'url': url, 'checked_at': NOW.isoformat(), 'permissions': permissions}}


class RelayCryptoTests(unittest.TestCase):
    def test_round_trip_randomized_ciphertext_has_no_source_plaintext(self):
        private, public = keys()
        payload = {'schema_version': 1, 'source': PRIVATE_TEXT, 'korean': '비공개 자료'}
        first, second = crypto.encrypt_payload(payload, public), crypto.encrypt_payload(payload, public)
        self.assertNotEqual(first, second)
        self.assertNotIn(PRIVATE_TEXT.encode(), first)
        self.assertEqual(crypto.decrypt_payload(first, private), payload)
        self.assertLessEqual(len(first), crypto.MAX_ENVELOPE_BYTES)

    def test_wrong_recipient_tampering_and_unknown_fields_fail_closed(self):
        private, public = keys()
        envelope = crypto.encrypt_payload({'secret': PRIVATE_TEXT}, public)
        other_private, _ = keys()
        with self.assertRaises(ValueError):
            crypto.decrypt_payload(envelope, other_private)
        for field in ('ciphertext', 'nonce', 'salt', 'ephemeral_public_key'):
            with self.subTest(field=field):
                value = json.loads(envelope)
                original = bytearray(base64.b64decode(value[field]))
                original[-1] ^= 1
                value[field] = base64.b64encode(original).decode()
                with self.assertRaises(ValueError):
                    crypto.decrypt_payload(crypto.canonical_json(value), private)
        value = json.loads(envelope)
        value['plaintext'] = PRIVATE_TEXT
        with self.assertRaises(ValueError):
            crypto.decrypt_payload(crypto.canonical_json(value), private)

    def test_payload_and_envelope_limits_and_duplicate_json(self):
        private, public = keys()
        with self.assertRaises(ValueError):
            crypto.encrypt_payload({'large': 'x' * crypto.MAX_PAYLOAD_BYTES}, public)
        with self.assertRaises(ValueError):
            crypto.decrypt_payload(b'x' * (crypto.MAX_ENVELOPE_BYTES + 1), private)
        with self.assertRaises(ValueError):
            crypto.decrypt_payload(b'{"version":1,"version":1}', private)
        with patch.object(crypto, 'MAX_ENVELOPE_BYTES', 128):
            with self.assertRaises(ValueError):
                crypto.encrypt_payload({'value': 'small'}, public)

    def test_key_file_format_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            private_path, public_path = Path(directory) / 'private.hex', Path(directory) / 'public.json'
            key_id = crypto.generate_key_files(private_path, public_path)
            private = crypto.decode_key(private_path.read_text())
            public = crypto.load_public_key(public_path)
            self.assertEqual(len(private), 32)
            self.assertEqual(len(public), 32)
            self.assertEqual(key_id, json.loads(crypto.encrypt_payload({}, public))['recipient_key_id'])
            self.assertEqual(crypto.decrypt_payload(crypto.encrypt_payload({}, public), private), {})
            before = private_path.read_bytes()
            with self.assertRaises(ValueError):
                crypto.generate_key_files(private_path, public_path)
            self.assertEqual(private_path.read_bytes(), before)
        for malformed in ('', 'a' * 63, 'z' * 64, base64.b64encode(bytes(32)).decode()):
            with self.assertRaises(ValueError):
                crypto.decode_key(malformed)

    def test_fingerprint_is_order_independent_and_changes_with_permissions(self):
        self.assertEqual(crypto.source_fingerprint({'b': 2, 'a': 1}), crypto.source_fingerprint({'a': 1, 'b': 2}))
        self.assertNotEqual(crypto.source_fingerprint({'fetch': 'allowed'}), crypto.source_fingerprint({'fetch': 'denied'}))


class RelayCollectorTests(unittest.TestCase):
    def test_three_approved_sources_actual_times_partial_failure_and_observation(self):
        called = []
        clock_values = iter([NOW, NOW + timedelta(seconds=3), NOW + timedelta(seconds=5), NOW + timedelta(seconds=8)])

        def fetch(source):
            called.append(source['id'])
            if source['id'] == 'korea-briefing':
                return SourceDocuments([document()], complete=False, coverage_note='앞 2페이지', listing_count=20, total_count=45, has_more=True)
            if source['id'] == 'korea-ministry-briefings':
                request = httpx.Request('GET', f'https://www.korea.kr/?secret={PRIVATE_TEXT}')
                error = httpx.HTTPStatusError(PRIVATE_TEXT, request=request, response=httpx.Response(503, request=request))
                raise ValueError(PRIVATE_TEXT) from error
            return SourceDocuments([], observation=observation())

        payload = collector.collect_payload(config(), fetcher=fetch, clock=lambda: next(clock_values))
        self.assertEqual(called, list(crypto.SOURCE_IDS))
        self.assertEqual(payload['generated_at'], '2026-11-07T12:00:08Z')
        first, failed, alerts = payload['sources']
        self.assertEqual(first['checked_at'], '2026-11-07T12:00:00Z')
        self.assertEqual(first['status'], 'ok')
        self.assertFalse(first['complete'])
        self.assertEqual(first['documents'][0]['body'], PRIVATE_TEXT)
        self.assertNotIn('listing_excerpt', first['documents'][0])
        self.assertEqual(first['listing_count'], 20)
        self.assertTrue(first['has_more'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['checked_at'], '2026-11-07T12:00:03Z')
        self.assertIn('HTTP 503', failed['error'])
        self.assertNotIn(PRIVATE_TEXT, failed['error'])
        self.assertFalse(failed['complete'])
        self.assertEqual(failed['documents'], [])
        self.assertEqual(alerts['observation']['total_count'], 2)
        self.assertEqual(alerts['observation']['matched_count'], 0)
        self.assertEqual(alerts['checked_at'], '2026-11-07T12:00:05Z')
        source = next(s for s in config()['sources'] if s['id'] == 'korea-briefing')
        self.assertEqual(first['config_fingerprint'], crypto.source_fingerprint(source))

    def test_noncommercial_unverified_or_mismatched_bodies_never_enter_packet(self):
        source = next(s for s in config()['sources'] if s['id'] == 'korea-briefing')
        cases = [document('KOGL-2-noncommercial'), document('KOGL-3'), document()]
        cases[2]['rights_verified'] = False
        mismatch = document()
        mismatch['rights']['url'] += '4'
        cases.append(mismatch)
        denied = document()
        denied['rights']['permissions']['llm'] = 'unknown'
        cases.append(denied)
        for original in cases:
            with self.subTest(rights=original['rights']['license']):
                result = collector._document(source, original)
                self.assertEqual(result['body'], '')
                self.assertNotIn('listing_excerpt', result)
                self.assertNotIn('rights', result)
                self.assertFalse(result['rights_verified'])
                self.assertEqual(result['permissions']['retention'], 'metadata_only')
                self.assertEqual(original['body'], PRIVATE_TEXT)

    def test_invalid_config_fails_before_any_fetch(self):
        original = config()
        cases = []
        missing = json.loads(json.dumps(original))
        missing['sources'] = [s for s in missing['sources'] if s['id'] != 'korea-briefing']
        cases.append(missing)
        for key, value in [('fetch_url', 'https://evil.example/briefing/pressReleaseList.do'), ('enabled', False), ('parser', 'rss_metadata')]:
            changed = json.loads(json.dumps(original))
            next(s for s in changed['sources'] if s['id'] == 'korea-briefing')[key] = value
            cases.append(changed)
        for bad in cases:
            fetch = Mock()
            with self.assertRaises(ValueError):
                collector.collect_payload(bad, fetcher=fetch)
            fetch.assert_not_called()

    def test_missing_monitor_data_is_failure_not_empty_success(self):
        payload = collector.collect_payload(config(), fetcher=lambda _: SourceDocuments([]), clock=lambda: NOW)
        alerts = payload['sources'][2]
        self.assertEqual(alerts['status'], 'failed')
        self.assertFalse(alerts['complete'])
        self.assertIsNone(alerts['observation'])

    def test_cli_writes_only_ciphertext_and_does_not_log_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_path, public_path = root / 'private.hex', root / 'public.json'
            crypto.generate_key_files(private_path, public_path)
            config_path = root / 'sources.json'
            config_path.write_text(json.dumps(config()), encoding='utf-8')
            output = root / 'encrypted.json'
            stdout, stderr = io.StringIO(), io.StringIO()
            payload = {'schema_version': 1, 'secret': PRIVATE_TEXT}
            with patch.object(collector, 'collect_payload', return_value=payload), redirect_stdout(stdout), redirect_stderr(stderr):
                code = collector.main(['--config', str(config_path), '--public-key', str(public_path), '--output', str(output)])
            self.assertEqual(code, 0)
            self.assertNotIn(PRIVATE_TEXT, output.read_text())
            self.assertNotIn(PRIVATE_TEXT, stdout.getvalue() + stderr.getvalue())
            self.assertEqual(crypto.decrypt_payload(output.read_bytes(), crypto.decode_key(private_path.read_text())), payload)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['encrypted.json', 'private.hex', 'public.json', 'sources.json'])
            before = output.read_bytes()
            with patch.object(collector, 'collect_payload', side_effect=RuntimeError(PRIVATE_TEXT)), redirect_stdout(stdout), redirect_stderr(stderr):
                code = collector.main(['--config', str(config_path), '--public-key', str(public_path), '--output', str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(output.read_bytes(), before)
            self.assertNotIn(PRIVATE_TEXT, stdout.getvalue() + stderr.getvalue())


if __name__ == '__main__':
    unittest.main()

