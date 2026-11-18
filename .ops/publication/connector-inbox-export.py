"""Export one receipt-bound public tree. The operator checkout and ledger are read only."""
import argparse
import base64
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace

REF = "refs/heads/public-export"
REPO = "Realhaapy/jeonjaeng-nalkka"
CHUNK = 12000
LIMIT = 1_000_000
SHA256 = re.compile(r"[0-9a-f]{64}")
PUBLIC_SUFFIXES = {".html", ".css", ".js", ".json", ".svg", ".xml", ".txt"}


class ExportError(Exception):
    """Only constant operator-facing descriptions may be used here."""


def require(ok, message):
    if not ok:
        raise ExportError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def serialized(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def read_state(root, path):
    require(path.parent == root / "data/cloud-publication-plans", "Unexpected plan directory")
    state = json.loads((root / "data/last-run.json").read_bytes())
    run = state["run_id"]
    pub = state["publication"]
    require(re.fullmatch("[0-9a-f]{32}", run) and path.name == run + ".json"
            and Path(pub["plan"]).resolve() == path and pub.get("mode") == "github_connector"
            and SHA256.fullmatch(pub["plan_sha256"]) and not pub.get("superseded")
            and pub.get("status") != "superseded", "Current receipt does not own this plan")
    return state


def bound(root, path, state, preflight):
    plan_raw = path.read_bytes()
    pin = state["publication"]["plan_sha256"]
    require(digest(plan_raw) == pin, "Plan digest changed")
    plan = json.loads(plan_raw)
    runner = module(root / "scripts/cloud-run.py")
    confirmed, owner, _ = runner.current_connector_plan(root, str(path))
    require(confirmed == plan and owner["publication"]["plan_sha256"] == pin
            and runner.activation_status(root)["ready"], "Current active plan or operator changed")
    reader = module(root / "scripts/connector-plan-read.py")
    tree = reader.git_tree_sha(plan["expected_tree"])
    require(plan["run_id"] == state["run_id"] and plan["repository"] == REPO
            and plan["expected_ref_sha"] == plan["base_commit_sha"] and plan["force"] is False
            and plan["ref"] == "heads/main" and re.fullmatch(r"r-[0-9a-f]{16}", plan["release_id"]),
            "Unexpected publication target or release")
    generated = plan["generated_at"]
    require(isinstance(generated, str) and len(generated) <= 40
            and datetime.fromisoformat(generated.replace("Z", "+00:00")).tzinfo is not None,
            "Missing UTC offset in release time")
    if preflight:
        args = SimpleNamespace(root=str(root), plan=str(path), mode="preflight", index=None, plan_sha256=pin)
        checked = reader.read(args)
        require(checked.get("remote_parent_checked") is True and checked["plan_sha256"] == pin
                and checked["expected_tree_sha"] == tree and checked["base_commit_sha"] == plan["base_commit_sha"]
                and checked["repository"] == REPO and checked["deletion_count"] == 0,
                "Existing publication preflight did not verify this plan")
        for entry in plan["entries"]:
            require(not runner.SECRET.search(entry["content"]) and not runner.PRIVATE_FIELD.search(entry["content"]),
                    "Private content in a public entry")
    meta = dict(plan_sha256=pin, run_id=plan["run_id"], base_commit_sha=plan["base_commit_sha"],
                expected_tree_sha=tree, release_id=plan["release_id"], generated_at=generated)
    pub = owner["publication"]
    if owner.get("status") == "succeeded" and pub.get("verified") is True and re.fullmatch("[0-9a-f]{40}", pub.get("commit", "")):
        meta["verified_commit"] = pub["commit"]
    return plan, meta


def directory(root):
    user = str(os.getuid()) if hasattr(os, "getuid") else "user"
    # tempfile's implicit fallback is the process working directory. Do not
    # probe it when a scheduled worker has a read-only canonical mount.
    candidates = [tempfile.tempdir, *(os.environ.get(k) for k in ("TMPDIR","TEMP","TMP"))]
    if os.name == "posix":
        candidates.extend(("/tmp","/var/tmp","/workspace/scratch"))
    for candidate in dict.fromkeys(candidates):
        if not candidate:
            continue
        temp=Path(candidate).resolve()
        if temp.is_relative_to(root) or not temp.is_dir():
            continue
        cache=temp/("warwatch-publication-inbox-"+user)
        try:
            cache.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            continue
        status=cache.lstat()
        require(stat.S_ISDIR(status.st_mode) and not cache.is_symlink(), "Unsafe scratch directory")
        if hasattr(os,"getuid"):
            require(status.st_uid==os.getuid() and status.st_mode&0o077==0, "Unsafe scratch owner or mode")
        if os.access(cache,os.W_OK):
            return cache
    raise ExportError("A writable private temporary directory outside the operator root is required")


def create_bundle(root, plan, meta, scratch):
    base = plan["base_commit_sha"]
    objects = root / "data/public-checkout/.git/objects"
    require(objects.is_dir() and not objects.is_symlink(), "Public baseline objects unavailable")
    env = {k: v for k,v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_ALTERNATE_OBJECT_DIRECTORIES=str(objects), GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_NO_REPLACE_OBJECTS="1",
               GIT_AUTHOR_NAME="Warwatch Publisher", GIT_AUTHOR_EMAIL="publisher@warwatch.invalid",
               GIT_COMMITTER_NAME="Warwatch Publisher", GIT_COMMITTER_EMAIL="publisher@warwatch.invalid",
               GIT_AUTHOR_DATE=plan["generated_at"], GIT_COMMITTER_DATE=plan["generated_at"])
    repo = scratch / "export.git"

    def git(*args, data=None):
        run = subprocess.run(["git","--git-dir",str(repo),"-c","pack.threads=1", *args],
                             input=data, env=env, capture_output=True, timeout=180)
        require(run.returncode == 0, "Scratch Git operation failed at " + args[0])
        return run.stdout

    git("init","--bare","--quiet")
    (repo / "shallow").write_bytes((base + "\n").encode("ascii"))
    require(git("rev-parse", base+"^{tree}").decode().strip() == plan["base_tree_sha"], "Baseline tree changed")
    git("read-tree", base)
    rows = bytearray()
    seen = set()
    for entry in plan["entries"]:
        name = entry["path"]
        require(name.startswith("public/") and name not in seen
                and not any(p.casefold() in {"",".","..",".git"} for p in name.split("/"))
                and not any(ord(c)<32 or ord(c)==127 or c in "\\:" for c in name)
                and not name.startswith(("public/design/","public/demo/design/"))
                and (Path(name).suffix.lower() in PUBLIC_SUFFIXES or name=="public/.nojekyll")
                and entry.get("mode")=="100644" and entry.get("type")=="blob" and "sha" not in entry
                and isinstance(entry.get("content"),str) and "\0" not in entry["content"], "Non-public or unsupported file change")
        seen.add(name)
        content = entry["content"].encode("utf-8")
        require(plan["prepared_public_sha256"].get(name[7:]) == digest(content), "Blob differs from inspected live output")
        blob = git("hash-object","-w","--stdin",data=content).decode().strip()
        require(plan["expected_tree"].get(name) == dict(type="blob",mode="100644",sha=blob), "Public blob differs from plan")
        rows.extend(("100644 "+blob+"\t"+name+"\0").encode("utf-8"))
    require(seen, "Empty public delta")
    git("update-index","-z","--index-info",data=bytes(rows))
    tree = git("write-tree").decode().strip()
    require(tree == meta["expected_tree_sha"], "Complete public tree differs from plan")
    delta = git("diff-tree","-r","--no-commit-id","--no-renames","--raw","-z",base,tree).split(b"\0")[:-1]
    changed = {}
    for change, item in zip(delta[::2],delta[1::2]):
        before,after,_,_,status=change.decode().split()
        name=item.decode()
        require(after=="100644" and (before,status) in {(":100644","M"),(":000000","A")},
                "Only regular public file additions and updates are allowed")
        require(status=="A" or not name.removeprefix("public/").startswith(
                ("releases/","snapshots/","demo/releases/","demo/snapshots/","assets/","demo/assets/")),
                "Immutable public history or assets changed")
        changed[name]=status
    require(set(changed)==seen and len(seen)<=1024, "Declared changes differ from allowed tree delta")
    release_path="public/releases/"+plan["release_id"]+".json"
    require("public/latest.json" in changed and changed.get(release_path)=="A", "Missing new immutable public release")
    latest_raw = git("show",tree+":public/latest.json")
    require(latest_raw==git("show",tree+":"+release_path), "Immutable release differs from latest")
    latest = json.loads(latest_raw)
    previous = json.loads(git("show",base+":public/latest.json"))
    require(latest["mode"]=="live" and latest["release_id"]==plan["release_id"] and latest["generated_at"]==plan["generated_at"]
            and datetime.fromisoformat(latest["generated_at"].replace("Z","+00:00")) >
                datetime.fromisoformat(previous["generated_at"].replace("Z","+00:00")),
            "Public release identity differs from plan")
    commit = git("commit-tree",tree,"-p",base,"-m","Publish briefing "+latest["generated_at"]).decode().strip()
    require(git("rev-list","--parents","-1",commit).decode().split()==[commit,base], "Unexpected export ancestry")
    git("update-ref",REF,commit)
    out=scratch/"public.bundle"
    git("bundle","create","--version=2",str(out),REF,"^"+base)
    git("bundle","verify",str(out))
    require(git("bundle","list-heads",str(out)).decode().strip()==commit+" "+REF, "Unexpected export refs")
    require(out.stat().st_size*4//3 < LIMIT, "Public envelope exceeds inbox size limit")
    return out.read_bytes()


def cache_file(cache, state, pin):
    require(SHA256.fullmatch(pin or ""), "Envelope SHA-256 required")
    return cache / (state["run_id"]+"-"+state["publication"]["plan_sha256"]+"-"+pin+".json")


def load_cached(cache, state, pin):
    path = cache_file(cache,state,pin)
    fd = os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_BINARY",0))
    with os.fdopen(fd,"rb") as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and 0 < st.st_size <= LIMIT, "Invalid cache file")
        data = stream.read(LIMIT+1)
    require(digest(data)==pin, "Public cache digest changed")
    return data


def export(root, path, mode, pin, offset):
    state = read_state(root,path)
    if mode == "chunk":
        data = load_cached(directory(root),state,pin)
        require(isinstance(offset,int) and 0<=offset<len(data) and offset%CHUNK==0, "Invalid chunk offset")
        chunk = data[offset:offset+CHUNK]
        return dict(offset=offset,bytes=len(chunk),sha256=digest(chunk),chunk=chunk.decode("ascii"))
    plan,meta = bound(root,path,state,mode!="bound")
    if mode=="bound":
        return meta
    cache=directory(root)
    if mode=="preflight":
        data=load_cached(cache,state,pin)
    else:
        with tempfile.TemporaryDirectory(dir=cache) as temp:
            scratch=Path(temp)
            bundle=create_bundle(root,plan,meta,scratch)
            fields={k:meta[k] for k in ("run_id","base_commit_sha","expected_tree_sha","release_id","generated_at")}
            data=serialized(dict(protocol=1,**fields,bundle_sha256=digest(bundle),
                                 bundle_base64=base64.b64encode(bundle).decode("ascii")))
            require(len(data)<=LIMIT,"Public envelope exceeds inbox size limit")
            private=scratch/"envelope.json"
            private.write_bytes(data)
            os.chmod(private,0o600)
            pin=digest(data)
            dest=cache_file(cache,state,pin)
            if dest.exists():
                load_cached(cache,state,pin)
            else:
                # Same-digest concurrent producers may replace one another with
                # identical bytes. Both names are on the same scratch filesystem.
                os.replace(private,dest)
    if mode=="full":
        return json.loads(data)
    return dict(**meta,bytes=len(data),sha256=digest(data),chunk_chars=CHUNK,
                blob_sha=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--plan",required=True,type=Path)
    parser.add_argument("--mode",choices=("full","bound","meta","chunk","preflight"),default="full")
    parser.add_argument("--offset",type=int)
    parser.add_argument("--sha256")
    args=parser.parse_args()
    # Prevent optional index refresh writes in the existing preflight.
    os.environ["GIT_OPTIONAL_LOCKS"]="0"
    os.environ["GIT_TERMINAL_PROMPT"]="0"
    sys.dont_write_bytecode=True
    try:
        result=export(args.root.resolve(),args.plan.resolve(),args.mode,args.sha256,args.offset)
    except Exception as exc:
        result={"error":str(exc) if isinstance(exc,ExportError) else "Public export validation failed",
                "error_type":type(exc).__name__}
        print(serialized(result).decode("ascii"))
        return 1
    print(serialized(result).decode("ascii"))
    return 0


if __name__=="__main__":
    sys.exit(main())
