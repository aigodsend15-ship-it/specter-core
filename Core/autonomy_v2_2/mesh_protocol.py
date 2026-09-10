from __future__ import annotations
import hashlib, json, time, uuid
from typing import Any, Dict

SMP_VERSION="1"
KINDS={"TASK","ACK","FINDING","CHALLENGE","PROPOSAL","VERDICT","CHECKPOINT","RECEIPT","INCIDENT","CAPACITY","HANDOFF"}
REQUIRED=("smp","message_id","project_id","branch_id","from","to","kind","created_at","body","content_sha256")

def canonical_json(obj: Any) -> str:
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False)

def envelope_digest(envelope: Dict[str,Any]) -> str:
    body={k:v for k,v in envelope.items() if k!="content_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()

def make_envelope(sender: str,recipient: str,kind: str,body: Dict[str,Any],*,project_id: str="specter-core",branch_id: str="main",checkpoint_id: str="") -> Dict[str,Any]:
    k=str(kind).upper()
    if k not in KINDS: raise ValueError("UNKNOWN_SMP_KIND")
    env={"smp":SMP_VERSION,"message_id":"msg-"+uuid.uuid4().hex[:20],"project_id":str(project_id),
         "branch_id":str(branch_id),"checkpoint_id":str(checkpoint_id),"from":str(sender),"to":str(recipient),
         "kind":k,"created_at":time.time(),"body":dict(body)}
    env["content_sha256"]=envelope_digest(env)
    return env

def verify_envelope(envelope: Dict[str,Any]) -> bool:
    try:
        if not isinstance(envelope,dict) or any(k not in envelope for k in REQUIRED): return False
        if str(envelope["smp"])!=SMP_VERSION or str(envelope["kind"]).upper() not in KINDS: return False
        if not isinstance(envelope["body"],dict): return False
        return str(envelope["content_sha256"])==envelope_digest(envelope)
    except Exception:
        return False
