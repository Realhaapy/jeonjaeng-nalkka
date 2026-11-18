"""Validate one public bundle without checking it out, then create its run-specific ref."""
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

SENDER_REF = 'refs/heads/public-export'
HEAD = 'refs/warwatch/incoming'
SHA = r'[0-9a-f]{40}'
FIELDS = {'protocol', 'run_id', 'base_commit_sha', 'expected_tree_sha', 'release_id',
          'generated_at', 'bundle_sha256', 'bundle_base64'}
TEXT = {'.html', '.json', '.xml', '.css', '.js', '.txt', '.svg'}
PROTECTED = ('releases/', 'snapshots/', 'demo/releases/', 'demo/snapshots/', 'assets/', 'demo/assets/')
SECRET = re.compile(rb'(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
PRIVATE = re.compile(rb'"(?:raw_text|raw_html|api_key|OPENAI_API_KEY|review_note)"\s*:')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def json_object(pairs):
    require(len(dict(pairs)) == len(pairs), 'Duplicate JSON keys')
    return dict(pairs)


def git(*args):
    result = subprocess.run(['git', '-c', 'pack.threads=1', '-c', 'core.hooksPath=' + os.devnull,
                             *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90)
    require(result.returncode == 0, 'Git operation failed at ' + args[0])
    return result.stdout


def remote_sha(ref):
    rows = git('ls-remote','--refs','origin',ref).decode('ascii').splitlines()
    if not rows:
        return None
    sha, name = rows[0].split('\t')
    require(len(rows) == 1 and re.fullmatch(SHA, sha) and name == ref, 'Remote ref mismatch')
    return sha


def envelope():
    # Read the committed regular blob at the event SHA; never follow a working-tree link.
    spec = 'HEAD:.ops/publication/incoming.json'
    require(0 < int(git('cat-file','-s',spec)) <= 1_000_000, 'Inbox exceeds its byte limit')
    data = json.loads(git('cat-file','blob',spec).decode('ascii'), object_pairs_hook=json_object)
    require(isinstance(data,dict) and set(data) == FIELDS and type(data['protocol']) is int
            and data['protocol'] == 1, 'Unexpected inbox schema')
    for field, pattern in {'run_id':r'[0-9a-f]{32}', 'base_commit_sha':SHA, 'expected_tree_sha':SHA,
                           'release_id':r'r-[0-9a-f]{16}', 'bundle_sha256':r'[0-9a-f]{64}'}.items():
        require(isinstance(data[field], str) and re.fullmatch(pattern, data[field]), 'Invalid identifier')
    stamp = data['generated_at']
    require(isinstance(stamp,str) and len(stamp) <= 40 and datetime.fromisoformat(stamp).utcoffset() is not None,
            'Invalid release timestamp')
    binary = base64.b64decode(data['bundle_base64'], validate=True)
    require(hashlib.sha256(binary).hexdigest() == data['bundle_sha256'], 'Bundle SHA256 mismatch')
    header, separator, body = binary[:8192].partition(b'\n\n')
    require(separator and body.startswith(b'PACK'), 'Malformed bundle header')
    lines = header.decode('ascii').splitlines()
    require(lines[0] in ('# v2 git bundle','# v3 git bundle'), 'Unsupported bundle version')
    if lines[0] == '# v3 git bundle' and lines[1:2] == ['@object-format=sha1']:
        del lines[1]
    require(len(lines) == 3 and lines[1].split(' ',1)[0] == '-' + data['base_commit_sha']
            and re.fullmatch(SHA + ' ' + SENDER_REF, lines[2]), 'Unexpected bundle prerequisite or ref')
    return data, binary, lines[2].split()[0]


def check_text(content):
    content.decode('utf-8')
    require(b'\0' not in content and not SECRET.search(content) and not PRIVATE.search(content),
            'Unexpected private or binary content')


def validate_changes(data, commit):
    base = data['base_commit_sha']
    require(git('cat-file','-t',commit).strip() == b'commit', 'Bundle tip is not a commit')
    require(int(git('cat-file','-s',commit)) <= 8192, 'Oversize commit object')
    check_text(git('cat-file','commit',commit))
    require(git('show','-s','--format=%P %T',commit).decode().strip() == base + ' ' + data['expected_tree_sha'],
            'Commit parent or full tree mismatch')
    rows = git('diff-tree','-r','-z','--raw','--no-renames','--no-abbrev',base,commit).split(b'\0')[:-1]
    require(0 < len(rows) <= 2048 and len(rows) % 2 == 0, 'Unexpected changed-file count')
    changed = {}
    total = 0
    for raw, name in zip(rows[::2], rows[1::2]):
        before, after, _, blob, status = raw.decode('ascii').split()
        require((before,status) in {(':000000','A'),(':100644','M')} and after == '100644',
                'Only regular public file additions and edits are accepted')
        path = name.decode('utf-8')
        require(path.startswith('public/') and not re.search(r'[\\:\x00-\x1f\x7f]',path)
                and all(s.casefold() not in ('','.','..','.git') for s in path.split('/'))
                and (Path(path).suffix.lower() in TEXT or path == 'public/.nojekyll'), 'Unexpected public path')
        relative = path[7:]
        require(not relative.startswith(('design/','demo/design/'))
                and not (status == 'M' and relative.startswith(PROTECTED)), 'Published history or assets changed')
        size = int(git('cat-file','-s',blob))
        total += size
        require(size <= 25_000_000 and total <= 100_000_000, 'Expanded publication exceeds limits')
        check_text(git('cat-file','blob',blob))
        changed[path] = status
    release = 'public/releases/' + data['release_id'] + '.json'
    require('public/latest.json' in changed and changed.get(release) == 'A', 'Missing new immutable release')
    latest_raw = git('show',commit + ':public/latest.json')
    require(latest_raw == git('show',commit + ':' + release), 'Immutable release differs from latest')
    latest = json.loads(latest_raw, object_pairs_hook=json_object)
    require(latest.get('mode') == 'live' and latest.get('release_id') == data['release_id']
            and latest.get('generated_at') == data['generated_at'], 'Release identity mismatch')
    previous = json.loads(git('show',base + ':public/latest.json'))
    require(datetime.fromisoformat(latest['generated_at']) > datetime.fromisoformat(previous['generated_at']),
            'Release time does not advance')


def stage():
    data, binary, commit = envelope()
    base = data['base_commit_sha']
    require(remote_sha('refs/heads/main') == base, 'Main has moved')
    git('fetch','--quiet','--depth=1','--no-tags','origin','+refs/heads/main:refs/warwatch/base')
    require(git('rev-parse','refs/warwatch/base').decode().strip() == base, 'Fetched main has moved')
    with tempfile.TemporaryDirectory() as scratch:
        bundle = Path(scratch) / 'public.bundle'
        bundle.write_bytes(binary)
        git('bundle','verify',str(bundle))
        git('-c','fetch.fsckObjects=true','-c','fetch.unpackLimit=1','fetch','--no-tags','--no-recurse-submodules',
            '--no-write-fetch-head',str(bundle),'+' + SENDER_REF + ':' + HEAD)
    require(git('rev-parse',HEAD).decode().strip() == commit, 'Bundle tip mismatch')
    validate_changes(data,commit)
    ref = 'refs/heads/ari-ready-' + data['run_id']
    require(remote_sha('refs/heads/main') == base, 'Main changed during verification')
    current = remote_sha(ref)
    if current is None:
        # An empty expected value makes this an atomic create; even a racing fast-forward is refused.
        git('push','--quiet','--no-follow-tags','--recurse-submodules=no',
            '--force-with-lease=' + ref + ':','origin',commit + ':' + ref)
    require(remote_sha(ref) == commit, 'Ready ref conflicts with this verified commit')
    print(json.dumps({'stage_ref':ref,'commit':commit,'tree':data['expected_tree_sha'],'release':data['release_id']}))


if __name__ == '__main__':
    os.environ['GIT_NO_REPLACE_OBJECTS'] = '1'
    os.environ['GIT_TERMINAL_PROMPT'] = '0'
    if os.name == 'posix':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (1536 << 20, 1536 << 20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (512 << 20, 512 << 20))
    stage()
