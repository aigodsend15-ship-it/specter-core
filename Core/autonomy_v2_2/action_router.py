from __future__ import annotations
from typing import Any, Callable, Dict
from .governance import GovernanceGate

class GovernedActionRouter:
    """Only locally authorized typed actions may cross into an executor."""
    def __init__(self,gate: GovernanceGate,executor: Callable[[str,Dict[str,Any],str,str],Dict[str,Any]]):
        self.gate=gate; self.executor=executor

    def execute(self,operation: Dict[str,Any],authorization: Dict[str,Any]) -> Dict[str,Any]:
        op_id=str(operation.get("operation_id") or "")
        capability=str(operation.get("capability") or "")
        args=dict(operation.get("args") or {})
        scope=str(operation.get("scope") or "")
        policy_hash=str(operation.get("policy_hash") or "")
        if not all((op_id,capability,scope,policy_hash)): raise ValueError("INCOMPLETE_ACTION_CONTRACT")
        self.gate.require(authorization,op_id,capability,args,scope,policy_hash)
        result=dict(self.executor(capability,args,scope,op_id) or {})
        receipt=self.gate.issue_receipt(authorization,op_id,capability,args,scope,policy_hash,result)
        if not self.gate.verify_receipt(receipt,authorization,op_id,capability,args,scope,policy_hash):
            raise RuntimeError("UNVERIFIED_ACTION_RECEIPT")
        return {"operation_id":op_id,"result":result,"receipt":receipt}
