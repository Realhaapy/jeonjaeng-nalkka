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
import struct
import subprocess
import sys
import zlib
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


def git_object(kind, content):
    prefix = kind.encode("ascii")+b" "+str(len(content)).encode("ascii")+b"\0"
    return hashlib.sha1(prefix+content).hexdigest()


def tree_objects(files):
    """Use Git's byte ordering (directories compare as names ending in '/')."""
    root = {}
    for path, entry in files.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part,{})
        node[parts[-1]] = (entry["mode"],entry["sha"])
    objects = {}

    def visit(directory):
        rows = []
        for name,item in directory.items():
            key = name.encode("utf-8")
            folder = isinstance(item,dict)
            mode,sha = ("40000",visit(item)) if folder else item
            rows.append((key+(b"/" if folder else b""),mode.encode()+b" "+key+b"\0"+bytes.fromhex(sha)))
        content = b"".join(row for _,row in sorted(rows))
        sha = git_object("tree",content)
        objects[sha]=content
        return sha

    top = visit(root)
    return top,objects


def baseline(root,plan):
    checkout = root/"data/public-checkout"
    base = plan["base_commit_sha"]
    env = {k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0",GIT_NO_REPLACE_OBJECTS="1",GIT_NO_LAZY_FETCH="1",GIT_TERMINAL_PROMPT="0")

    def git(*args, data=None):
        proc = subprocess.run(["git","-C",str(checkout),*args],input=data,env=env,capture_output=True,timeout=120)
        require(proc.returncode==0,"Cannot read existing public baseline")
        return proc.stdout

    top=git("rev-parse",base+"^{tree}").decode().strip()
    require(top==plan["base_tree_sha"],"Baseline tree changed")
    files, trees = {}, {top}
    for row in git("ls-tree","--full-tree","-r","-t","-z",base).split(b"\0")[:-1]:
        info,name=row.split(b"\t",1)
        mode,kind,sha=info.decode().split()
        if kind=="tree":
            trees.add(sha)
        else:
            require(kind=="blob" and mode in {"100644","100755"},"Unsupported baseline entry")
            files[name.decode("utf-8")]=dict(type=kind,mode=mode,sha=sha)
    return files,trees,git


def previous_blobs(git, candidates, required_sha):
    """Read only reachable public blobs. Oversize optional delta bases are skipped."""
    ids=sorted(set(candidates)|{required_sha})
    names=("\n".join(ids)+"\n").encode("ascii")
    rows=git("cat-file","--batch-check",data=names).splitlines()
    require(len(rows)==len(ids),"Invalid baseline size response")
    sizes={}
    budget=100_000_000
    for sha,row in zip(ids,rows):
        oid,kind,size=row.decode().split()
        require(oid==sha and kind=="blob","Invalid baseline blob")
        size=int(size)
        if size<=25_000_000 and (size<=budget or sha==required_sha):
            sizes[sha]=size
            budget-=size
    require(required_sha in sizes,"Previous public release exceeds the size limit")
    names=("\n".join(sizes)+"\n").encode("ascii")
    result=git("cat-file","--batch",data=names)
    pos=0
    contents={}
    for sha,size in sizes.items():
        header=(sha+" blob "+str(size)+"\n").encode()
        require(result.startswith(header,pos),"Invalid baseline object header")
        pos+=len(header)
        value=result[pos:pos+size]
        pos+=size
        require(result[pos:pos+1]==b"\n" and git_object("blob",value)==sha,"Baseline object content mismatch")
        contents[sha]=value
        pos+=1
    require(pos==len(result),"Unexpected baseline bytes")
    return contents


def varint(number):
    out=bytearray()
    while number>=128:
        out.append(128|(number&127))
        number>>=7
    out.append(number)
    return out


def copy_opcode(offset,length):
    # Git's copy opcode describes which little-endian offset and size bytes follow.
    instruction=bytearray([128])
    for i,value in enumerate(offset.to_bytes(4,"little")+length.to_bytes(3,"little")):
        if value:
            instruction[0]|=1<<i
            instruction.append(value)
    return instruction


def delta(old,new):
    result=varint(len(old))+varint(len(new))
    width=32
    anchors={}
    for start in range(0,len(old)-width+1,64):
        anchors.setdefault(old[start:start+width],start)
    def literals(left,right):
        for start in range(left,right,127):
            part=new[start:min(start+127,right)]
            result.append(len(part))
            result.extend(part)
    start=pos=0
    while pos+width<=len(new):
        key=new[pos:pos+width]
        origin=anchors.get(key)
        if origin is None:
            pos+=1
            continue
        if old[pos:pos+width]==key:
            origin=pos
        a,b=origin,pos
        while b>start and a>0 and old[a-1]==new[b-1]:
            a-=1;b-=1
        length=pos+width-b
        maximum=min(len(old)-a,len(new)-b,0xffffff)
        while length+1024<=maximum and old[a+length:a+length+1024]==new[b+length:b+length+1024]:
            length+=1024
        while length<maximum and old[a+length]==new[b+length]:
            length+=1
        literals(start,b)
        result.extend(copy_opcode(a,length))
        start=pos=b+length
    literals(start,len(new))
    return result


def pack_entry(kind,content,reference=b""):
    size=len(content)
    header=bytearray([(kind<<4)|(size&15)])
    size>>=4
    while size:
        header[-1]|=128
        header.append(size&127)
        size>>=7
    return bytes(header)+reference+zlib.compress(content,6)


def create_bundle(root,plan,meta):
    old,old_trees,git=baseline(root,plan)
    expected=plan["expected_tree"]
    require(old.keys()<=expected.keys(),"Public path deletion is prohibited")
    changes={p for p in expected if expected[p]!=old.get(p)}
    require(0<len(changes)<=1024,"Unexpected public delta size")
    blobs,possible,seen={},{},set()
    total=0
    for entry in plan["entries"]:
        name=entry["path"]
        require(name.startswith("public/") and name in changes and name not in seen
                and not any(p.casefold() in {"",".","..",".git"} for p in name.split("/"))
                and not any(ord(c)<32 or ord(c)==127 or c in "\\:" for c in name)
                and not name.startswith(("public/design/","public/demo/design/"))
                and (Path(name).suffix.lower() in PUBLIC_SUFFIXES or name=="public/.nojekyll")
                and entry.get("mode")=="100644" and entry.get("type")=="blob" and "sha" not in entry
                and isinstance(entry.get("content"),str) and "\0" not in entry["content"],"Unsupported public file change")
        prior=old.get(name)
        require(not prior or (prior["mode"]=="100644" and not name[7:].startswith(
                ("releases/","snapshots/","demo/releases/","demo/snapshots/","assets/","demo/assets/"))),
                "Immutable public history or asset changed")
        raw=entry["content"].encode("utf-8")
        total+=len(raw)
        require(len(raw)<=25_000_000 and total<=100_000_000,"Public delta exceeds size limit")
        require(plan["prepared_public_sha256"].get(name[7:])==digest(raw),"Blob differs from inspected live output")
        oid=git_object("blob",raw)
        require(expected[name]==dict(type="blob",mode="100644",sha=oid),"Public blob differs from plan")
        seen.add(name)
        blobs[oid]=raw
        if prior:
            possible[oid]=prior["sha"]
        elif name.startswith(("public/releases/","public/demo/releases/")):
            earlier="public/demo/latest.json" if "/demo/" in name else "public/latest.json"
            if earlier in old:
                possible.setdefault(oid,old[earlier]["sha"])
    require(seen==changes,"Declared changes differ from true public delta")
    tree,trees=tree_objects(expected)
    require(tree==meta["expected_tree_sha"],"Complete public tree differs from plan")
    release="public/releases/"+plan["release_id"]+".json"
    require("public/latest.json" in changes and release in changes and release not in old
            and expected[release]==expected["public/latest.json"],"Missing equivalent new immutable release")
    previous=old["public/latest.json"]["sha"]
    retained=previous_blobs(git,possible.values(),previous)
    latest=json.loads(blobs[expected[release]["sha"]])
    stamp=datetime.fromisoformat(plan["generated_at"].replace("Z","+00:00"))
    require(latest["mode"]=="live" and latest["release_id"]==plan["release_id"]
            and latest["generated_at"]==plan["generated_at"]
            and stamp>datetime.fromisoformat(json.loads(retained[previous])["generated_at"].replace("Z","+00:00")),
            "Public release identity differs from plan")
    identity="Warwatch Publisher <publisher@warwatch.invalid> "+str(int(stamp.timestamp()))+" "+stamp.strftime("%z")
    base=plan["base_commit_sha"]
    commit=("tree "+tree+"\nparent "+base+"\nauthor "+identity+"\ncommitter "+identity+
            "\n\nPublish briefing "+plan["generated_at"]+"\n").encode("ascii")
    commit_sha=git_object("commit",commit)
    packed=[pack_entry(1,commit)]
    for oid in sorted(trees.keys()-old_trees):
        packed.append(pack_entry(2,trees[oid]))
    existing={row["sha"] for row in old.values()}
    for oid in sorted(blobs.keys()-existing):
        raw=blobs[oid]
        full=pack_entry(3,raw)
        parent=possible.get(oid)
        if parent in retained:
            compact=pack_entry(7,delta(retained[parent],raw),bytes.fromhex(parent))
            if len(compact)<len(full):
                full=compact
        packed.append(full)
    pack=b"PACK"+struct.pack(">II",2,len(packed))+b"".join(packed)
    header=("# v2 git bundle\n-"+base+" public base\n"+commit_sha+" "+REF+"\n\n").encode("ascii")
    return header+pack+hashlib.sha1(pack).digest()


def envelope(root,path,mode):
    state=read_state(root,path)
    plan,meta=bound(root,path,state,mode!="bound")
    if mode=="bound":
        return meta,None
    bundle=create_bundle(root,plan,meta)
    fields={k:meta[k] for k in ("run_id","base_commit_sha","expected_tree_sha","release_id","generated_at")}
    data=serialized(dict(protocol=1,**fields,bundle_sha256=digest(bundle),bundle_base64=base64.b64encode(bundle).decode("ascii")))
    require(len(data)<=LIMIT,"Public envelope exceeds inbox size limit")
    return dict(**meta,bytes=len(data),sha256=digest(data),chunk_chars=CHUNK,blob_sha=git_object("blob",data)),data


def chunk(data,offset):
    require(isinstance(offset,int) and 0<=offset<len(data) and offset%CHUNK==0,"Invalid chunk offset")
    piece=data[offset:offset+CHUNK]
    return dict(offset=offset,bytes=len(piece),sha256=digest(piece),chunk=piece.decode("ascii"))


def export(root,path,mode,pin=None,offset=None):
    meta,data=envelope(root,path,mode)
    if mode in ("preflight","chunk"):
        require(pin==meta["sha256"],"Pinned public envelope changed")
    if mode=="chunk":
        return chunk(data,offset)
    if mode=="full":
        return json.loads(data)
    return meta


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
        root,path=args.root.resolve(),args.plan.resolve()
        result=export(root,path,args.mode,args.sha256,args.offset)
    except Exception as exc:
        result={"error":str(exc) if isinstance(exc,ExportError) else "Public export validation failed",
                "error_type":type(exc).__name__}
        print(serialized(result).decode("ascii"))
        return 1
    print(serialized(result).decode("ascii"))
    return 0


if __name__=="__main__":
    sys.exit(main())
