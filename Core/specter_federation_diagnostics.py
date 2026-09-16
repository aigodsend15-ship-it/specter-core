"""Read-only service probes, kept separate from the federation peer registry."""
import json,os,urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
DB_PATH=Path(os.getenv('SPECTER_DB_PATH',str(Path(__file__).resolve().parent/'storage/specter_fabric.sqlite3')))
class SpecterFederationHub:
    def __init__(self,db_path=None):pass
    def sync_federation(self):
        endpoints={'specter-local-core':os.getenv('SPECTER_LOCAL_ENDPOINT','http://127.0.0.1:8080')+'/health','huggingface-space':os.getenv('SPECTER_MESH_ENDPOINT','https://pintograndao-hermes-bridge.hf.space')+'/health'}
        def probe(item):
            name,url=item
            try:
                with urllib.request.urlopen(url,timeout=8) as r:
                    data=json.load(r)
                    return name,{'ok':r.status==200 and data.get('status') in ['healthy','ok']}
            except Exception as exc:return name,{'ok':False,'error':type(exc).__name__}
        with ThreadPoolExecutor(max_workers=2) as pool:measurements=dict(pool.map(probe,endpoints.items()))
        count=sum(x['ok'] for x in measurements.values())
        return {'federation_status':'REACHABLE' if count==len(endpoints) else 'DEGRADED','active_peer_count':count,'measurements':measurements}
