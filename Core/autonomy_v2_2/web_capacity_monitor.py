from __future__ import annotations
import sys, time
from typing import Any, Dict, List, Optional

PROVIDERS = {
    "chatgpt.com": "chatgpt",
    "claude.ai": "claude",
    "grok.com": "grok",
    "gemini.google.com": "gemini",
}

class BrowserCapacityObserver:
    """Read-only browser observer for visible provider throttling notices."""
    def __init__(self):
        for p in (r"C:\Users\USER\.hermes", r"C:\Specter\Core"):
            if p not in sys.path: sys.path.insert(0,p)
        from codex_browser_bridge.client import CodexBrowserClient
        self.client=CodexBrowserClient()

    @staticmethod
    def provider(url: str) -> Optional[str]:
        low=(url or "").lower()
        for host,name in PROVIDERS.items():
            if host in low: return name
        return None

    def sample(self) -> List[Dict[str,Any]]:
        out=[]
        try: tabs=self.client.list_tabs()
        except Exception: return out
        js="""(()=>{const xs=[...document.querySelectorAll('[role=dialog], [role=alert], [aria-modal=true]')];
        const texts=xs.filter(x=>x.offsetParent!==null).map(x=>(x.innerText||'').trim()).filter(Boolean);
        const keys=['too many requests','temporarily limited','please wait a few minutes','rate limit'];
        const hit=texts.find(t=>keys.some(k=>t.toLowerCase().includes(k))) || '';
        return {visibility:document.visibilityState,hit:hit.slice(0,4000)};})()"""
        for tab in tabs:
            provider=self.provider(str(tab.get("url") or ""))
            tid=tab.get("id")
            if not provider or not isinstance(tid,int): continue
            try:
                state=self.client.evaluate(tid,js) or {}
            except Exception:
                continue
            hit=str(state.get("hit") or "").strip()
            if hit:
                out.append({"provider":provider,"tab_id":tid,"url":str(tab.get("url") or ""),"visibility":state.get("visibility"),"text":hit,"ts":time.time()})
        return out
