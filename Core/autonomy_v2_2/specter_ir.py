from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

MAGIC = "SXL/1"
OPS = {"TASK","FACT","PROPOSE","EVIDENCE","STATE","NEXT","RECEIPT","HANDOFF"}

@dataclass(frozen=True)
class SXLRecord:
    op: str
    body: Dict[str, Any]

    def canonical(self) -> str:
        return f"{MAGIC} {self.op} " + json.dumps(self.body, ensure_ascii=False, sort_keys=True, separators=(",",":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

class SpecterExchangeLanguage:
    """Compact typed agent interchange. It carries facts/proposals, never authority."""
    @staticmethod
    def encode(op: str, body: Dict[str, Any]) -> str:
        op = str(op).upper().strip()
        if op not in OPS: raise ValueError("UNKNOWN_SXL_OP")
        if not isinstance(body, dict): raise TypeError("BODY_MUST_BE_OBJECT")
        return SXLRecord(op, body).canonical()
    @staticmethod
    def decode_line(line: str) -> SXLRecord:
        raw = str(line).strip()
        if not raw.startswith(MAGIC + " "): raise ValueError("BAD_SXL_MAGIC")
        rest = raw[len(MAGIC)+1:]
        op, sep, payload = rest.partition(" ")
        op = op.upper().strip()
        if not sep or op not in OPS: raise ValueError("BAD_SXL_OP")
        obj = json.loads(payload)
        if not isinstance(obj, dict): raise ValueError("BAD_SXL_BODY")
        return SXLRecord(op, obj)

    @classmethod
    def decode_many(cls, text: str) -> List[SXLRecord]:
        out=[]
        for line in str(text).splitlines():
            if line.strip().startswith(MAGIC + " "):
                out.append(cls.decode_line(line))
        return out

    @staticmethod
    def handoff(task_ref: str, from_agent: str, facts: Iterable[dict], proposals: Iterable[dict], receipts: Iterable[str]=()) -> str:
        body={"task_ref":task_ref,"from_agent":from_agent,"facts":list(facts),"proposals":list(proposals),"receipts":list(receipts)}
        return SpecterExchangeLanguage.encode("HANDOFF",body)

    @staticmethod
    def is_executable(record: SXLRecord) -> bool:
        # PROPOSE remains a proposal until external policy/approval validates it.
        return False
