"""Install on the existing AstrBot host as an SSH forced-command, not a news worker."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.request
import urllib.error
import uuid
from urllib.parse import urlsplit

def backup(policy):
    mount = Path(policy["mount"]).resolve()
    if not os.path.ismount(mount):
        raise RuntimeError("backup_mount_missing")
    data = Path(policy["data"]).resolve()
    target = data / "knowledge_base" / policy["kb_id"]
    destination = mount / "backups/astrbot/deployments" / (
        "nyaanews-" + time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12])
    destination.mkdir(parents=True, mode=0o700)
    files = [data / "knowledge_base/kb.db"] + list(target.rglob("*"))
    manifest = []
    for source in files:
        if not source.is_file() or source.is_symlink() or source.name.endswith(("-wal", "-shm")):
            continue
        output = destination / source.relative_to(data)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if source.suffix == ".db":
            with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(output)) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("backup_integrity_failure")
        else:
            shutil.copy2(source, output)
        output.chmod(0o600)
        manifest.append({"path": str(source.relative_to(data)),
                         "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                         "bytes":output.stat().st_size})
    if not any(x["path"] == "knowledge_base/kb.db" for x in manifest):
        raise RuntimeError("backup_missing_database")
    receipt = {"kb_id":policy["kb_id"],"files":manifest,
               "note":"Per-database consistent snapshots; not a cross-index atomic recovery guarantee"}
    (destination/"manifest.json").write_text(json.dumps(receipt,indent=2))
    (destination/"manifest.json").chmod(0o600)
    return str(destination)

def allowed(method, path, policy):
    base = "/api/v1/knowledge-bases"
    route = base + "/" + policy["kb_id"]
    plain = urlsplit(path).path
    if "#" in path or urlsplit(path).scheme or urlsplit(path).netloc or ".." in path or "%" in plain:
        return False
    if method == "POST" and plain == "/api/v1/auth/login":
        return True
    if method == "GET" and plain == base:
        return True
    if method == "GET" and re.fullmatch(base + r"/tasks/[a-zA-Z0-9-]+", plain):
        return True
    if method == "GET" and (plain in (route, route+"/stats",route+"/documents",route+"/chunks") or
        re.fullmatch(re.escape(route)+r"/documents/[a-zA-Z0-9-]+", plain)):
        return True
    if method == "POST" and plain in (route+"/documents/import",route+"/retrieve"):
        return True
    return method == "DELETE" and bool(re.fullmatch(re.escape(route)+r"/documents/[a-zA-Z0-9-]+",plain))

def forward(method, path, payload, token, policy):
    headers = {"Content-Type":"application/json"}
    if token:
        headers["Authorization"] = "Bearer "+token
    request = urllib.request.Request(policy["base"]+path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,method=method)
    try:
        with urllib.request.urlopen(request,timeout=90) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code,json.load(exc)

def serve(policy, request):
    method, path = request.get("method"), request.get("path","")
    if not allowed(method,path,policy):
        raise ValueError("route_denied")
    payload = request.get("body")
    token = request.get("token")
    snapshot = None
    plain=urlsplit(path).path
    base="/api/v1/knowledge-bases"
    route=base+"/"+policy["kb_id"]
    tasks_path=Path(policy["data"]).parent/"nyaanews-gateway/tasks.json"
    if method=="GET" and plain.startswith(base+"/tasks/"):
        owned=json.loads(tasks_path.read_text()) if tasks_path.exists() else []
        if plain.rsplit("/",1)[1] not in owned:
            raise ValueError("task_scope_denied")
    if (method=="GET" and plain==base) or (method=="POST" and plain==route+"/retrieve"):
        code, details=forward("GET",route,None,token,policy)
        if code!=200 or details.get("status")!="ok":
            return {"http_status":code,"body":details,"backup":None}
        if method=="GET":
            details["data"]={"items":[details["data"]],"total":1}
            return {"http_status":code,"body":details,"backup":None}
        payload=dict(payload or {},kb_names=[details["data"]["kb_name"]])
    if method == "POST" and path.endswith("/documents/import"):
        docs = (payload or {}).get("documents",[])
        if not 1 <= len(docs) <= 3 or any(not d.get("file_name","").startswith("nyaanews-") for d in docs):
            raise ValueError("document_scope_denied")
        snapshot = backup(policy)
    if method == "DELETE":
        code, body = forward("GET",path,None,token,policy)
        doc = body.get("data") or {}
        if code != 200 or not doc.get("doc_name",doc.get("file_name","")).startswith("nyaanews-"):
            raise ValueError("delete_scope_denied")
        snapshot = backup(policy)
    code, body = forward(method,path,payload,token,policy)
    if method=="POST" and plain==route+"/documents/import" and code==200:
        task_id=(body.get("data") or {}).get("task_id")
        if task_id:
            owned=json.loads(tasks_path.read_text()) if tasks_path.exists() else []
            owned.append(task_id)
            tasks_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            temp=tasks_path.with_suffix("."+uuid.uuid4().hex+".tmp")
            temp.write_text(json.dumps(owned))
            temp.chmod(0o600)
            os.replace(temp,tasks_path)
    return {"http_status":code,"body":body,"backup":snapshot}

if __name__ == "__main__":
    try:
        policy=json.loads(Path(sys.argv[1]).read_text())
        raw=sys.stdin.buffer.read(2_000_001)
        if len(raw)>2_000_000:
            raise ValueError("request_too_large")
        print(json.dumps(serve(policy,json.loads(raw))))
    except Exception as exc:
        # Never print exception bodies, requests, credentials or tokens.
        print(json.dumps({"gateway_error":type(exc).__name__}))
        raise SystemExit(1)
