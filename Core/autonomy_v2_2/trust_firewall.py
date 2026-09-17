from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Dict, List

_PATTERNS = {
    "authority_claim": [r"\bi am (?:the )?(?:owner|admin|system|developer)\b", r"\bignore (?:the )?(?:system|policy|previous)\b"],
    "policy_redefinition": [r"\b(?:new|updated) (?:policy|rules?|constitution)\b", r"\bdisable (?:guard|safety|approval|policy)\b"],
    "secret_request": [r"\b(?:password|cookie|session token|api key|seed phrase|private key)\b"],
    "tool_request": [r"\b(?:run|execute|delete|upload|publish|shell|powershell|cmd\.exe)\b"],
    "instruction_like": [r"\b(?:you must|do this now|follow these instructions|system message)\b"],
}

@dataclass(frozen=True)
class FirewallFinding:
    kind: str
    excerpt: str
    confidence: float

class UntrustedContextFirewall:
    """Deterministic provenance firewall for web/RAG/tool/agent text."""
    def inspect(self, text: str, source: str="untrusted") -> Dict[str, Any]:
        raw=str(text or "")
        findings: List[FirewallFinding]=[]
        for kind, patterns in _PATTERNS.items():
            for pat in patterns:
                m=re.search(pat,raw,re.I)
                if m:
                    findings.append(FirewallFinding(kind, raw[max(0,m.start()-80):m.end()+120], 0.9))
                    break
        high={"authority_claim","policy_redefinition","secret_request"}
        med={"tool_request","instruction_like"}
        kinds={f.kind for f in findings}
        risk="high" if kinds & high else ("medium" if kinds & med else "none")
        handling="quarantine" if risk=="high" else ("pass_with_labels" if risk=="medium" else "pass")
        return {
            "source":source,
            "risk":risk,
            "clean_content":raw,
            "findings":[{"type":f.kind,"excerpt":f.excerpt,"confidence":f.confidence} for f in findings],
            "authority_claims":[{"claim":f.excerpt,"verified":False} for f in findings if f.kind=="authority_claim"],
            "recommended_handling":handling,
            "authority_granted":False,
        }

    def envelope(self, text: str, source: str) -> Dict[str, Any]:
        inspected=self.inspect(text,source)
        return {
            "schema":"specter.context.envelope.v1",
            "provenance":{"source":source,"trusted":False},
            "inspection":inspected,
            "data":text,
        }
