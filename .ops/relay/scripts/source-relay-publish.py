"""Publish only an encrypted relay envelope to the dedicated repository branch."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

REPOSITORY = "Realhaapy/jeonjaeng-nalkka"
BRANCH = "source-relay"
FILE = "encrypted/latest.json"
API = f"https://api.github.com/repos/{REPOSITORY}/contents/{FILE}"


def request(method, url, token, body=None):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "JeonjaengNalkka-source-relay"}
    data = None if body is None else json.dumps(body).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--envelope", type=Path, required=True)
    args = parser.parse_args()
    # Local invocation without Actions context must never publish.
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Publication is restricted to the approved repository Actions runner")
    raw = args.envelope.read_bytes()
    if not 100 < len(raw) <= 4 * 1024 * 1024:
        raise ValueError("Invalid encrypted envelope size")
    envelope = json.loads(raw)
    fields = {"format", "version", "recipient_key_id", "ephemeral_public_key", "salt", "nonce", "ciphertext"}
    if (not isinstance(envelope, dict) or set(envelope) != fields
            or envelope.get("format") != "warwatch-source-relay"
            or type(envelope.get("version")) is not int or envelope["version"] != 1
            or not isinstance(envelope.get("ciphertext"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(envelope.get("recipient_key_id", "")))):
        raise ValueError("Only an encrypted envelope may be published")
    for name, length in (("ephemeral_public_key", 32), ("salt", 32), ("nonce", 12)):
        if len(base64.b64decode(envelope[name], validate=True)) != length:
            raise ValueError("Invalid encrypted envelope parameter")
    if not 16 <= len(base64.b64decode(envelope["ciphertext"], validate=True)) <= 3 * 1024 * 1024 + 16:
        raise ValueError("Invalid ciphertext size")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("Built-in repository token is unavailable")
    body = {"branch": BRANCH, "message": "ops: refresh encrypted official-source relay",
            "content": base64.b64encode(raw).decode("ascii")}
    try:
        existing = request("GET", API + "?ref=" + BRANCH, token)
        body["sha"] = existing["sha"]
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise RuntimeError(f"Relay lookup failed: HTTP {error.code}") from None
    try:
        result = request("PUT", API, token, body)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Relay publication failed: HTTP {error.code}") from None
    commit = result.get("commit", {}).get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("Relay publication did not return a commit")
    print(json.dumps({"branch": BRANCH, "commit": commit, "encrypted_bytes": len(raw)}))


if __name__ == "__main__":
    main()
