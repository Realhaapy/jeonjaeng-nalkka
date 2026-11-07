"""Offline publisher boundary checks; no real token, network, or ledger access."""
import base64
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/source-relay-publish.py'
SPEC = importlib.util.spec_from_file_location('source_relay_publish_tested', SCRIPT)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def encoded(value):
    return base64.b64encode(value).decode('ascii')


class SourceRelayPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='warwatch-publisher-test-')
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.temp.cleanup)
        self.path = self.root / 'envelope.json'
        self.environment = {'GITHUB_ACTIONS': 'true',
                            'GITHUB_REPOSITORY': publisher.REPOSITORY,
                            'GITHUB_TOKEN': 'OFFLINE_FAKE_TOKEN'}
        # Synthetic envelope shape only; never represents collected source data.
        self.envelope = {'format': 'warwatch-source-relay', 'version': 1,
                         'recipient_key_id': 'a' * 64,
                         'ephemeral_public_key': encoded(b'k' * 32),
                         'salt': encoded(b's' * 32), 'nonce': encoded(b'n' * 12),
                         'ciphertext': encoded(b'c' * 32)}

    def invoke(self, *, envelope=None, raw=None, environment=None,
               responses=(), error=None, calls=0):
        value = self.envelope if envelope is None else envelope
        if raw is None:
            raw = json.dumps(value, indent=2).encode('utf-8')
        self.path.write_bytes(raw)
        environment = self.environment if environment is None else environment
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(sys, 'argv', ['publisher', '--envelope', str(self.path)]), \
             patch.object(publisher, 'request', side_effect=responses) as request, \
             patch('urllib.request.urlopen', side_effect=AssertionError('Network prohibited')) as network, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            if error is not None:
                with self.assertRaises(error):
                    publisher.main()
            else:
                publisher.main()
            self.assertEqual(request.call_count, calls)
            network.assert_not_called()
            requests = request.call_args_list
        for call in requests:
            method, url, token = call.args[:3]
            self.assertEqual(token, 'OFFLINE_FAKE_TOKEN')
            self.assertEqual(url, publisher.API + '?ref=source-relay' if method == 'GET' else publisher.API)
            if method == 'PUT':
                body = call.args[3]
                self.assertEqual(body['branch'], 'source-relay')
                self.assertLessEqual(set(body), {'branch', 'message', 'content', 'sha'})
                self.assertEqual(json.loads(base64.b64decode(body['content'])), value)
        if error is None:
            self.assertEqual(json.loads(output.getvalue())['branch'], 'source-relay')
            self.assertNotIn('OFFLINE_FAKE_TOKEN', output.getvalue())
        return requests

    def test_local_invocation_rejected(self):
        self.invoke(environment={}, error=ValueError)

    def test_wrong_repository_rejected(self):
        self.invoke(environment=dict(self.environment, GITHUB_REPOSITORY='other/repo'), error=ValueError)

    def test_missing_token_rejected(self):
        self.invoke(environment={k: v for k, v in self.environment.items() if k != 'GITHUB_TOKEN'}, error=ValueError)

    def test_extra_plaintext_field_rejected(self):
        self.invoke(envelope=dict(self.envelope, raw_text='OFFLINE_PLAINTEXT_SENTINEL'), error=ValueError)

    def test_boolean_version_rejected(self):
        self.invoke(envelope=dict(self.envelope, version=True), error=ValueError)

    def test_wrong_recipient_identifier_rejected(self):
        self.invoke(envelope=dict(self.envelope, recipient_key_id='bad'), error=ValueError)

    def test_invalid_nonce_length_rejected(self):
        self.invoke(envelope=dict(self.envelope, nonce=encoded(b'n' * 11)), error=ValueError)

    def test_invalid_base64_rejected(self):
        self.invoke(envelope=dict(self.envelope, ciphertext='not base64!'), error=ValueError)

    def test_short_ciphertext_rejected(self):
        self.invoke(envelope=dict(self.envelope, ciphertext=encoded(b'x' * 15)), error=ValueError)

    def test_update_is_pinned_and_uses_prior_blob_sha(self):
        calls = self.invoke(responses=[{'sha': 'b' * 40}, {'commit': {'sha': 'c' * 40}}], calls=2)
        self.assertEqual(calls[1].args[3]['sha'], 'b' * 40)

    def test_missing_file_can_create_only_at_fixed_destination(self):
        calls = self.invoke(responses=[HTTPError(publisher.API, 404, 'missing', {}, None),
                                      {'commit': {'sha': 'c' * 40}}], calls=2)
        self.assertNotIn('sha', calls[1].args[3])

    def test_lookup_denial_stops_without_write(self):
        self.invoke(responses=[HTTPError(publisher.API, 403, 'denied', {}, None)], error=RuntimeError, calls=1)

    def test_write_denial_is_not_retried(self):
        self.invoke(responses=[{'sha': 'b' * 40}, HTTPError(publisher.API, 403, 'denied', {}, None)],
                    error=RuntimeError, calls=2)

    def test_unknown_write_result_fails(self):
        self.invoke(responses=[{'sha': 'b' * 40}, {}], error=RuntimeError, calls=2)

    def test_duplicate_known_field_with_plaintext_is_rejected(self):
        raw = ('{"ciphertext":"OFFLINE_PLAINTEXT_SENTINEL",' + json.dumps(self.envelope)[1:]).encode('utf-8')
        self.invoke(raw=raw, error=ValueError)

    def test_upload_is_canonical_after_validation(self):
        calls = self.invoke(responses=[{'sha': 'b' * 40}, {'commit': {'sha': 'c' * 40}}], calls=2)
        actual = base64.b64decode(calls[1].args[3]['content'])
        expected = json.dumps(self.envelope, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False).encode('utf-8')
        self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
