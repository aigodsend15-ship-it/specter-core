from __future__ import annotations
import hashlib, hmac, json, os, secrets, time
from pathlib import Path
from typing import Any, Dict

class GovernanceGate:
    """Local authority gate. Agent/web text never creates authorization."""
    def __init__(self,key: bytes):
        if not isinstance(key,(bytes,bytearray)) or len(key)<32: raise ValueError("GOVERNANCE_KEY_TOO_SHORT")
        self.key=bytes(key)

    @classmethod
    def load_or_create(cls,path: str | Path):
        p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists(): key=p.read_bytes()
        else:
            key=secrets.token_bytes(32); p.write_bytes(key)
            try: os.chmod(p,0o600)
            except Exception: pass
        return cls(key)

    @staticmethod
    def _canon(obj: Dict[str,Any]) -> bytes:
        return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")

    @staticmethod
    def args_sha256(args: Dict[str,Any]) -> str:
        return hashlib.sha256(GovernanceGate._canon(dict(args))).hexdigest()
    def issue(self,operation_id: str,capability: str,args: Dict[str,Any],scope: str,policy_hash: str,ttl_s: int=300) -> Dict[str,Any]:
        now=time.time()
        grant={"schema":"specter.authorization.v1","operation_id":str(operation_id),"capability":str(capability),"args_sha256":self.args_sha256(args),"scope":str(scope),"policy_hash":str(policy_hash),"issued_at":now,"expires_at":now+max(1,int(ttl_s)),"nonce":secrets.token_hex(16)}
        sig=hmac.new(self.key,self._canon(grant),hashlib.sha256).hexdigest()
        return {**grant,"signature":sig}

    def verify(self,authorization: Dict[str,Any],operation_id: str,capability: str,args: Dict[str,Any],scope: str,policy_hash: str) -> bool:
        try:
            auth=dict(authorization); sig=str(auth.pop("signature"))
            expected=hmac.new(self.key,self._canon(auth),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,sig): return False
            if float(auth["expires_at"])<=time.time(): return False
            return auth.get("schema")=="specter.authorization.v1" and str(auth.get("operation_id"))==str(operation_id) and str(auth.get("capability"))==str(capability) and str(auth.get("scope"))==str(scope) and str(auth.get("policy_hash"))==str(policy_hash) and str(auth.get("args_sha256"))==self.args_sha256(args)
        except Exception:
            return False

    def authorization_sha256(self,authorization: Dict[str,Any]) -> str:
        return hashlib.sha256(self._canon(dict(authorization))).hexdigest()

    def sign_receipt(self,receipt: Dict[str,Any]) -> Dict[str,Any]:
        body=dict(receipt); body.pop("signature",None)
        sig=hmac.new(self.key,self._canon(body),hashlib.sha256).hexdigest()
        return {**body,"signature":sig}

    def verify_receipt(self,receipt: Dict[str,Any],authorization: Dict[str,Any]) -> bool:
        try:
            body=dict(receipt); sig=str(body.pop("signature"))
            expected=hmac.new(self.key,self._canon(body),hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected,sig) and str(body.get("authorization_sha256"))==self.authorization_sha256(authorization)
        except Exception:
            return False
    def require(self,authorization: Dict[str,Any],operation_id: str,capability: str,args: Dict[str,Any],scope: str,policy_hash: str) -> None:
        if not self.verify(authorization,operation_id,capability,args,scope,policy_hash):
            raise PermissionError("INVALID_LOCAL_AUTHORIZATION")

    def issue_receipt(self,authorization: Dict[str,Any],operation_id: str,capability: str,args: Dict[str,Any],scope: str,policy_hash: str,result: Dict[str,Any]) -> Dict[str,Any]:
        self.require(authorization,operation_id,capability,args,scope,policy_hash)
        result_body=dict(result)
        receipt={"schema":"specter.action.receipt.v1","operation_id":str(operation_id),"capability":str(capability),"args_sha256":self.args_sha256(args),"scope":str(scope),"policy_hash":str(policy_hash),"authorization_sha256":self.authorization_sha256(authorization),"result":result_body,"result_sha256":hashlib.sha256(self._canon(result_body)).hexdigest(),"ts":time.time()}
        return self.sign_receipt(receipt)

    def verify_receipt(self,receipt: Dict[str,Any],authorization: Dict[str,Any],operation_id: str="",capability: str="",args: Dict[str,Any]|None=None,scope: str="",policy_hash: str="") -> bool:
        try:
            body=dict(receipt); sig=str(body.pop("signature"))
            expected=hmac.new(self.key,self._canon(body),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,sig): return False
            if str(body.get("authorization_sha256"))!=self.authorization_sha256(authorization): return False
            if operation_id and str(body.get("operation_id"))!=str(operation_id): return False
            if capability and str(body.get("capability"))!=str(capability): return False
            if scope and str(body.get("scope"))!=str(scope): return False
            if policy_hash and str(body.get("policy_hash"))!=str(policy_hash): return False
            if args is not None and str(body.get("args_sha256"))!=self.args_sha256(args): return False
            result=dict(body.get("result") or {})
            return str(body.get("result_sha256"))==hashlib.sha256(self._canon(result)).hexdigest()
        except Exception:
            return False
