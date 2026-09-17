from __future__ import annotations
import hashlib, hmac, json, time, uuid
from typing import Any, Dict, Optional

SMP_VERSION = "1"
KINDS = {"HELLO","CAPACITY","TASK","ACK","FINDING","CHALLENGE","PROPOSAL","VERDICT","CHECKPOINT","RECEIPT","INCIDENT","HEARTBEAT","MEETING"}

def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def build_envelope(sender: str, recipient: str, kind: str, payload: Dict[str, Any], *, project_id: str="specter-core", branch_id: str="main", checkpoint_id: Optional[str]=None) -> Dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("UNKNOWN_SMP_KIND")
    env = {"smp": SMP_VERSION, "message_id": "msg-" + uuid.uuid4().hex[:20], "project_id": project_id, "branch_id": branch_id, "checkpoint_id": checkpoint_id, "from": sender, "to": recipient, "kind": kind, "payload": payload, "created_at": time.time()}
    env["content_sha256"] = sha256_text(canonical_json(env))
    return env

def verify_envelope(env: Dict[str, Any]) -> bool:
    if env.get("smp") != SMP_VERSION or env.get("kind") not in KINDS:
        return False
    supplied = str(env.get("content_sha256") or "")
    body = dict(env); body.pop("content_sha256", None)
    return bool(supplied) and hmac.compare_digest(supplied, sha256_text(canonical_json(body)))

class SMPAuthenticator:
    """Optional peer authentication. Secret stays local and outside model prompts."""
    def __init__(self, secret: bytes):
        if len(secret) < 16:
            raise ValueError("SMP_SECRET_TOO_SHORT")
        self.secret = secret
    def sign(self, env: Dict[str, Any]) -> Dict[str, Any]:
        if not verify_envelope(env):
            raise ValueError("INVALID_ENVELOPE")
        out = dict(env)
        out["peer_hmac_sha256"] = hmac.new(self.secret, canonical_json(env).encode("utf-8"), hashlib.sha256).hexdigest()
        return out
    def verify(self, env: Dict[str, Any]) -> bool:
        sig = str(env.get("peer_hmac_sha256") or "")
        body = dict(env); body.pop("peer_hmac_sha256", None)
        if not sig or not verify_envelope(body):
            return False
        expected = hmac.new(self.secret, canonical_json(body).encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
