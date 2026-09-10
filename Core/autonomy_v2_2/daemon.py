from __future__ import annotations
import argparse, json, os, signal, time
from pathlib import Path
from .autonomy_runtime import SovereignAutonomyRuntime


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',required=True)
    ap.add_argument('--interval',type=float,default=10.0)
    ap.add_argument('--no-browser-monitor',action='store_true')
    args=ap.parse_args()
    root=Path(args.root); root.mkdir(parents=True,exist_ok=True)
    pidf=root/'state'/'autonomy_v21.pid'; pidf.parent.mkdir(exist_ok=True)
    pidf.write_text(str(os.getpid()),encoding='ascii')
    rt=SovereignAutonomyRuntime(root)
    observer=None
    if not args.no_browser_monitor:
        try:
            from .web_capacity_monitor import BrowserCapacityObserver
            observer=BrowserCapacityObserver()
        except Exception:
            observer=None
    stop={'value':False}
    def halt(*_): stop['value']=True
    try: signal.signal(signal.SIGTERM,halt)
    except Exception: pass
    try:
        while not stop['value']:
            browser_incidents=[]
            if observer is not None:
                try:
                    for evt in observer.sample():
                        endpoint=rt.capacity.first_endpoint_for_provider(evt['provider'])
                        if endpoint:
                            rec=rt.report_provider_text(endpoint,evt['text'])
                            if rec.get('incident'):
                                browser_incidents.append({**rec,'provider':evt['provider'],'tab_id':evt['tab_id']})
                except Exception as exc:
                    browser_incidents.append({'monitor_error':type(exc).__name__+':'+str(exc)[:300]})
            status=rt.tick()
            status['browser_monitor']=observer is not None
            status['browser_incidents']=browser_incidents
            rt.status_file.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
            time.sleep(max(1.0,args.interval))
    finally:
        rt.close()
        try: pidf.unlink()
        except Exception: pass

if __name__=='__main__': main()
