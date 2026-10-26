"""A persistent outbox with read-before-retry reconciliation."""
import json
from pathlib import Path
import subprocess
import time
import uuid
import re
import sys
from urllib.parse import quote
from .util import digest, now

class Gateway:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = None

    def raw(self, method, path, body=None, authenticated=True):
        v = self.cfg.values
        key=v["GATEWAY_KEY"]
        known=v["GATEWAY_KNOWN_HOSTS"]
        if Path("/run/nyaanews-config").is_dir():
            key="/tmp/nyaanews-ssh/id_ed25519"
            known="/run/nyaanews-config/ssh/known_hosts"
        proxy=[]
        proxy_host=v.get("GATEWAY_SOCKS_HOST")
        if proxy_host and (v.get("GATEWAY_SOCKS_CONTAINER_ONLY")!="1" or Path("/run/nyaanews-config").is_dir()):
            proxy_port=v.get("GATEWAY_SOCKS_PORT","1080")
            if not re.fullmatch(r"[a-zA-Z0-9.-]+",proxy_host) or not proxy_port.isdigit() or not 1<=int(proxy_port)<=65535:
                raise ValueError("invalid_ssh_proxy")
            proxy=["-o",f"ProxyCommand={sys.executable} -m nyaanews.ssh_proxy {proxy_host} {proxy_port} %h %p"]
        cmd = ["ssh","-T",*proxy,"-o","BatchMode=yes","-o","ConnectTimeout=10",
               "-o","StrictHostKeyChecking=yes","-o","IdentitiesOnly=yes",
               "-o","UserKnownHostsFile="+known,
               "-i",key,"-p",v.get("GATEWAY_PORT","22"),
               v["GATEWAY_USER"]+"@"+v["GATEWAY_HOST"]]
        payload = {"method":method,"path":path,"body":body,
                   "token":self.token if authenticated else None}
        try:
            result = subprocess.run(cmd,input=json.dumps(payload).encode(),
                                    capture_output=True,timeout=110)
        except (subprocess.TimeoutExpired, OSError):
            raise RuntimeError("gateway_unavailable") from None
        if result.returncode:
            raise RuntimeError("gateway_failed")
        envelope = json.loads(result.stdout)
        if envelope.get("gateway_error"):
            raise RuntimeError("gateway_rejected")
        if method in ("POST","DELETE") and "/documents" in path and not envelope.get("backup"):
            raise RuntimeError("missing_remote_backup_receipt")
        return envelope

    def login(self):
        v = self.cfg.values
        result = self.raw("POST","/api/v1/auth/login",
                         {"username":v["ASTRBOT_USERNAME"],"password":v["ASTRBOT_PASSWORD"]}, False)
        body=result["body"]
        if result["http_status"]!=200 or body.get("status")!="ok" or not (body.get("data") or {}).get("token"):
            raise RuntimeError("authentication_action_required")
        self.token=body["data"]["token"]

    def call(self, method, path, body=None):
        if not self.token:
            self.login()
        result=self.raw(method,path,body)
        if result["http_status"]==401:
            self.login()
            # POST after an explicit 401 is unambiguously unauthorized.
            result=self.raw(method,path,body)
        response=result["body"]
        if result["http_status"]>=400 or response.get("status")!="ok":
            raise RuntimeError("astrbot_request_failed")
        return response.get("data")

def pages(gateway, path):
    items=[]
    for page in range(1,10001):
        data=gateway.call("GET",path+("&" if "?" in path else "?")+f"page={page}&page_size=100")
        batch=data.get("items",[])
        items.extend(batch)
        if len(items)>=data.get("total",len(items)) or len(batch)<100:
            return items
    raise RuntimeError("pagination_limit")

def chunks_for(meta, text, limit=900):
    # Character bound is deliberately conservative for multilingual embedding limits.
    header=f'{meta["title"]}\n{meta["published_at"]}\n{meta["canonical_url"]}\n'
    if len(header)>limit:
        raise ValueError("metadata_exceeds_chunk_limit")
    content=text.split("\n## Article\n\n",1)[-1]
    available=limit-len(header)
    pieces=[]
    current=""
    for paragraph in content.split("\n\n"):
        for offset in range(0,len(paragraph),available):
            part=paragraph[offset:offset+available]
            if current.strip() and len(current)+len(part)+2>available:
                pieces.append(header+current.strip())
                current=""
            current+=part+"\n\n"
    if current.strip():
        pieces.append(header+current.strip())
    if not pieces or len(pieces)>200:
        raise ValueError("article_chunk_budget")
    return pieces

def set_outbox(store, aid, **changes):
    with store.write() as db:
        keys=list(changes)
        db.execute("UPDATE outbox SET "+",".join(k+"=?" for k in keys)+" WHERE article_id=?",
                   [changes[k] for k in keys]+[aid])

def verify_document(gateway, route, doc, expected):
    doc_id=doc.get("doc_id",doc.get("document_id"))
    if not doc_id:
        return None
    details=gateway.call("GET",route+"/documents/"+doc_id)
    pieces=pages(gateway,route+"/chunks?document_id="+quote(doc_id))
    texts=[x.get("text",x.get("content","")) for x in pieces]
    # Block order from list APIs is not assumed.
    if len(texts)!=len(expected) or sorted(texts)!=sorted(expected):
        return None
    return doc_id

def sync_pending(cfg,store,gateway=None):
    gateway=gateway or Gateway(cfg)
    matches=[k for k in pages(gateway,"/api/v1/knowledge-bases")
             if k.get("kb_name",k.get("name"))==cfg.values["ASTRBOT_KB_NAME"]]
    if len(matches)!=1:
        raise RuntimeError("knowledge_base_not_unique")
    kb_id=matches[0].get("kb_id",matches[0].get("id"))
    route="/api/v1/knowledge-bases/"+kb_id
    report={"verified":0,"pending":0,"unknown":0,"failed":0}
    rows=store.db.execute("SELECT * FROM outbox WHERE status != 'verified'").fetchall()
    for row in rows:
        aid=row["article_id"]
        if row["kb_id"] and row["kb_id"]!=kb_id:
            report["failed"]+=1
            continue
        try:
            a=store.db.execute("SELECT * FROM articles WHERE id=?",(aid,)).fetchone()
            meta=json.loads(a["payload"])
            text=(cfg.data/a["path"]).read_text(encoding="utf-8")
            body=text.split("\n## Article\n\n",1)[1].rstrip("\n")
            if digest(body)!=meta["content_sha256"]:
                raise ValueError("archive_content_hash_mismatch")
            chunks=chunks_for(meta,text,int(cfg.values.get("CHUNK_CHAR_LIMIT","900")))
            filename=row["filename"] or f'nyaanews-{aid}-{a["body_hash"][:12]}.md'
            docs=pages(gateway,route+"/documents?search="+quote(filename))
            equal=[d for d in docs if d.get("doc_name",d.get("file_name"))==filename]
            if equal:
                doc_id=verify_document(gateway,route,equal[0],chunks) if len(equal)==1 else None
                if not doc_id:
                    set_outbox(store,aid,status="unknown")
                    report["unknown"]+=1
                    continue
                set_outbox(store,aid,status="verified",kb_id=kb_id,document_id=doc_id,filename=filename)
                report["verified"]+=1
                continue
            if row["status"] in ("submitted","unknown","processing"):
                # A missing document does not prove an interrupted POST was never accepted.
                report["unknown"]+=1
                continue
            attempt=uuid.uuid4().hex
            with store.write() as db:
                db.execute("INSERT INTO import_attempts VALUES (?,?,?,?)",
                    (attempt,aid,now().isoformat(),json.dumps({"filename":filename,"kb_id":kb_id})))
                db.execute("UPDATE outbox SET status='submitted',kb_id=?,filename=? WHERE article_id=?",
                           (kb_id,filename,aid))
            try:
                result=gateway.call("POST",route+"/documents/import",{
                    "documents":[{"file_name":filename,"file_type":"md","chunks":chunks}],
                    "batch_size":4,"tasks_limit":1,"max_retries":1})
                task_id=result["task_id"]
                set_outbox(store,aid,task_id=task_id,status="processing")
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    task=gateway.call("GET","/api/v1/knowledge-bases/tasks/"+task_id)
                    if task.get("status") in ("completed","failed"):
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError("task_timeout")
                result=task.get("result") or {}
                if task.get("status")!="completed" or result.get("success_count")!=1 or result.get("failed_count"):
                    # Definitive failures can be retried after reconciliation.
                    set_outbox(store,aid,status="failed")
                    report["failed"]+=1
                    continue
                uploaded=result.get("uploaded",[])
                doc_id=verify_document(gateway,route,uploaded[0],chunks) if len(uploaded)==1 else None
                if not doc_id:
                    raise RuntimeError("verification_mismatch")
                set_outbox(store,aid,status="verified",document_id=doc_id)
                report["verified"]+=1
            except Exception:
                set_outbox(store,aid,status="unknown")
                report["unknown"]+=1
        except Exception:
            report["failed"]+=1
    return report
