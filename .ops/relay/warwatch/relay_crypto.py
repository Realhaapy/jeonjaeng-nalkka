"""Bounded encrypted transport; branch provenance must authenticate the sender."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAX_PAYLOAD_BYTES = 3 * 1024 * 1024
MAX_ENVELOPE_BYTES = 4 * 1024 * 1024
REPOSITORY = 'Realhaapy/jeonjaeng-nalkka'
SOURCE_IDS = ('korea-briefing', 'korea-ministry-briefings', 'korea-disaster-messages')
FORMAT = 'warwatch-source-relay'
INFO = b'warwatch-source-relay/v1'


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def source_fingerprint(source):
    return hashlib.sha256(canonical_json(source)).hexdigest()


def decode_key(text):
    """Private/public key files use 64 hex characters, never PEM or base64."""
    if not isinstance(text, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', text.strip()):
        raise ValueError('Invalid relay key format')
    return bytes.fromhex(text.strip())


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate relay JSON field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Invalid relay JSON constant')


def _parse(data):
    try:
        return json.loads(data, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except (UnicodeError, TypeError, ValueError) as exc:
        raise ValueError('Invalid relay JSON') from exc


def load_public_key(path):
    data = Path(path).read_bytes()
    if len(data) > 4096:
        raise ValueError('Invalid relay public key file')
    value = _parse(data)
    if not isinstance(value, dict) or set(value) != {'algorithm', 'public_key'} or value['algorithm'] != 'X25519':
        raise ValueError('Invalid relay public key file')
    return decode_key(value['public_key'])


def _raw_public(key):
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _header(public):
    return {'format': FORMAT, 'version': 1, 'recipient_key_id': hashlib.sha256(public).hexdigest()}


def _derive(shared, salt):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=INFO).derive(shared)


def _b64(value):
    return base64.b64encode(value).decode('ascii')


def _unb64(value, length=None):
    try:
        if not isinstance(value, str):
            raise ValueError('Invalid encoding')
        decoded = base64.b64decode(value, validate=True)
        if length is not None and len(decoded) != length:
            raise ValueError('Invalid length')
        return decoded
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid relay envelope encoding') from exc


def encrypt_payload(payload, recipient_public_key):
    if not isinstance(payload, dict):
        raise ValueError('Relay payload must be an object')
    plain = canonical_json(payload)
    if len(plain) > MAX_PAYLOAD_BYTES:
        raise ValueError('Relay payload exceeds size limit')
    recipient = X25519PublicKey.from_public_bytes(recipient_public_key)
    ephemeral = X25519PrivateKey.generate()
    salt, nonce = os.urandom(32), os.urandom(12)
    header = _header(recipient_public_key)
    ciphertext = AESGCM(_derive(ephemeral.exchange(recipient), salt)).encrypt(nonce, plain, canonical_json(header))
    envelope = dict(header, ephemeral_public_key=_b64(_raw_public(ephemeral.public_key())),
                    salt=_b64(salt), nonce=_b64(nonce), ciphertext=_b64(ciphertext))
    encoded = canonical_json(envelope)
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise ValueError('Relay envelope exceeds size limit')
    return encoded


def decrypt_payload(envelope, recipient_private_key):
    if not isinstance(envelope, bytes) or len(envelope) > MAX_ENVELOPE_BYTES:
        raise ValueError('Invalid relay envelope size')
    value = _parse(envelope)
    fields = {'format', 'version', 'recipient_key_id', 'ephemeral_public_key', 'salt', 'nonce', 'ciphertext'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Invalid relay envelope fields')
    private = X25519PrivateKey.from_private_bytes(recipient_private_key)
    header = _header(_raw_public(private.public_key()))
    if type(value['version']) is not int or any(value.get(key) != item for key, item in header.items()):
        raise ValueError('Relay recipient or format mismatch')
    ephemeral = X25519PublicKey.from_public_bytes(_unb64(value['ephemeral_public_key'], 32))
    salt, nonce = _unb64(value['salt'], 32), _unb64(value['nonce'], 12)
    ciphertext = _unb64(value['ciphertext'])
    if not 16 <= len(ciphertext) <= MAX_PAYLOAD_BYTES + 16:
        raise ValueError('Invalid relay ciphertext size')
    try:
        plain = AESGCM(_derive(private.exchange(ephemeral), salt)).decrypt(nonce, ciphertext, canonical_json(header))
    except (InvalidTag, ValueError) as exc:
        raise ValueError('Relay envelope authentication failed') from exc
    if len(plain) > MAX_PAYLOAD_BYTES:
        raise ValueError('Relay payload exceeds size limit')
    payload = _parse(plain)
    if not isinstance(payload, dict):
        raise ValueError('Relay payload must be an object')
    return payload


def generate_key_files(private_path, public_path):
    """Ari-only provisioning helper: never overwrite or print a private key."""
    private_path, public_path = Path(private_path), Path(public_path)
    if private_path.resolve() == public_path.resolve() or private_path.exists() or public_path.exists():
        raise ValueError('Relay key destination already exists or overlaps')
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = _raw_public(private.public_key())
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='ascii') as stream:
        stream.write(raw.hex() + '\n')
    with public_path.open('x', encoding='utf-8') as stream:
        stream.write(canonical_json({'algorithm': 'X25519', 'public_key': public.hex()}).decode() + '\n')
    return hashlib.sha256(public).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Provision the Ari relay recipient key pair once.')
    parser.add_argument('--generate-key', required=True, metavar='PRIVATE_HEX_PATH')
    parser.add_argument('--public-key-output', required=True, metavar='PUBLIC_JSON_PATH')
    args = parser.parse_args(argv)
    try:
        key_id = generate_key_files(args.generate_key, args.public_key_output)
    except Exception:
        print('Relay key provisioning failed; existing files were not overwritten.', file=sys.stderr)
        return 1
    print(f'Relay public key fingerprint: {key_id}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
