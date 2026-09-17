from __future__ import annotations
import argparse, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from federation_hub_v22 import FederationHub

class Handler(BaseHTTPRequestHandler):
    hub: FederationHub = None
    server_version = "SpecterHub/2.2"
    def _send(self, code:int, obj):
        raw=json.dumps(obj,ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _json(self):
        n=min(int(self.headers.get("Content-Length","0") or 0),1024*1024)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
    def do_GET(self):
        if self.path=="/health": return self._send(200,{"ok":True,"service":"specter-federation-hub","version":"2.2"})
        if self.path=="/v1/status": return self._send(200,self.hub.summary())
        return self._send(404,{"error":"not_found"})
    def do_POST(self):
        try: body=self._json()
        except Exception: return self._send(400,{"error":"invalid_json"})
        try:
            if self.path=="/v1/peer/register":
                out=self.hub.register_peer(str(body["peer_id"]),str(body["kind"]),body.get("capabilities",[]),endpoint=body.get("endpoint"),evidence=body.get("evidence")); return self._send(200,out)
            if self.path=="/v1/message": return self._send(200,self.hub.ingest(body))
            if self.path=="/v1/meeting/open": return self._send(200,self.hub.open_meeting(str(body["objective"]),dict(body.get("members") or {})))
            if self.path=="/v1/meeting/close": self.hub.close_meeting(str(body["meeting_id"])); return self._send(200,{"ok":True})
        except Exception as exc:
            return self._send(409,{"error":type(exc).__name__,"detail":str(exc)[:300]})
        return self._send(404,{"error":"not_found"})
    def log_message(self, fmt, *args): return

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--db",default=r"C:\Specter\Core\storage\federation_v22.sqlite3"); ap.add_argument("--host",default="127.0.0.1"); ap.add_argument("--port",type=int,default=9765); args=ap.parse_args()
    hub=FederationHub(Path(args.db)); Handler.hub=hub; server=ThreadingHTTPServer((args.host,args.port),Handler)
    try: server.serve_forever()
    finally: server.server_close(); hub.close()

if __name__=="__main__": main()
