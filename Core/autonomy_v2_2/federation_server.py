from __future__ import annotations
import argparse, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from .federation_hub import FederationHub

class FederationHandler(BaseHTTPRequestHandler):
    hub: FederationHub = None
    server_version="SpecterFederation/1.0"

    def log_message(self,fmt,*args):
        return

    def _read(self) -> Dict[str,Any]:
        n=min(int(self.headers.get("Content-Length","0") or 0),1_048_576)
        raw=self.rfile.read(n) if n else b"{}"
        obj=json.loads(raw.decode("utf-8","replace"))
        if not isinstance(obj,dict): raise ValueError("OBJECT_REQUIRED")
        return obj

    def _send(self,code: int,obj: Dict[str,Any]):
        data=json.dumps(obj,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def _auth(self,obj: Dict[str,Any]) -> str:
        # Trusted transport adapter: authenticate peer before hub/policy sees the message.
        node=str(self.headers.get("X-Specter-Node", "")); sig=str(self.headers.get("X-Specter-Signature", ""))
        if not node or not sig or not self.hub.verify(node,obj,sig): raise PermissionError("BAD_NODE_SIGNATURE")
        return node

    def do_GET(self):
        if self.path=="/health":
            self._send(200,{"ok":True,"schema":"specter.federation.health.v1","ts":time.time(),"summary":self.hub.summary()})
        else: self._send(404,{"ok":False,"error":"NOT_FOUND"})

    def do_POST(self):
        try:
            obj=self._read()
            if self.path=="/enroll":
                out=self.hub.enroll(str(obj.get("token","")),str(obj.get("name","worker")),obj.get("capabilities") or [],str(obj.get("address","")))
                self._send(201,{"ok":True,**out}); return
            node=self._auth(obj)
            if self.path=="/heartbeat":
                self.hub.heartbeat(node,str(obj.get("address",""))); self._send(200,{"ok":True}); return
            if self.path=="/claim":
                job=self.hub.claim(node,int(obj.get("ttl_s",120))); self._send(200,{"ok":True,"job":job}); return
            if self.path=="/complete":
                out=self.hub.complete(node,str(obj["job_id"]),int(obj["lease_token"]),dict(obj.get("result") or {})); self._send(200,{"ok":True,"receipt":out}); return
            if self.path=="/message":
                out=self.hub.ingest_message(node,dict(obj.get("envelope") or {}),obj.get("recipients") or None); self._send(201,{"ok":True,**out}); return
            if self.path=="/messages":
                out=self.hub.pending_messages(node,int(obj.get("limit",50))); self._send(200,{"ok":True,"messages":out}); return
            if self.path=="/ack":
                ok=self.hub.ack_message(node,str(obj.get("message_id",""))); self._send(200,{"ok":True,"acked":ok}); return
            self._send(404,{"ok":False,"error":"NOT_FOUND"})
        except PermissionError as exc:
            self._send(403,{"ok":False,"error":str(exc)})
        except Exception as exc:
            self._send(400,{"ok":False,"error":type(exc).__name__+":"+str(exc)})
def serve(db_path: str,host: str="127.0.0.1",port: int=18777):
    hub=FederationHub(db_path); FederationHandler.hub=hub
    server=ThreadingHTTPServer((host,int(port)),FederationHandler)
    try: server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close(); hub.close()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default=str(Path.home()/".specter"/"federation.sqlite3"))
    ap.add_argument("--host",default="127.0.0.1",help="Use a private overlay/VPN address for federation; loopback is default.")
    ap.add_argument("--port",type=int,default=18777)
    args=ap.parse_args(); serve(args.db,args.host,args.port)

if __name__=="__main__": main()
