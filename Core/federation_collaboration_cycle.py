"""Bounded collaboration cycle: measurements, regression verification, local backup."""
import argparse,hashlib,json,os,subprocess,sys,time,uuid
from pathlib import Path
from federation_client import FederationClient
ROOT=Path(__file__).resolve().parent
STORAGE=ROOT/'storage'

def cycle(credentials):
    config=json.loads(credentials.read_text(encoding='utf-8'))
    control=FederationClient(config['endpoint'],config['control_key'])
    worker=FederationClient(config['endpoint'],config['worker_key'])
    statepath=STORAGE/'federation_cycle_state.json';STORAGE.mkdir(exist_ok=True)
    state=json.loads(statepath.read_text(encoding='utf-8')) if statepath.exists() else {}
    if int(state.get('last_cycle',0)//300)==int(time.time()//300) and len(state.get('results',[]))==3:
        return state['results']
    codehash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(ROOT.glob('*.py')))).hexdigest()
    results=[]
    snapshot_path=STORAGE/'federation_cloud_snapshot.json'
    if snapshot_path.exists() and not control.request('GET','/v1/export')['tasks']:
        saved=json.loads(snapshot_path.read_text(encoding='utf-8'))
        if saved.get('tasks'):control.request('POST','/v1/restore',saved)
    for node,role in [('Specter_Sentinel','service-diagnostics'),('Specter_Synthesizer','regression-verification'),('Hermes_Bridge_Worker','backup-transport')]:
        worker.request('POST','/v1/agents',{'id':node,'role':role})
        label={'Specter_Sentinel':'Measure public health','Specter_Synthesizer':'Verify changed code','Hermes_Bridge_Worker':'Export authenticated queue snapshot'}[node]
        task=control.request('POST','/v1/tasks',{'target':node,'prompt':json.dumps({'operation':label,'prior_evidence':results}), 'idempotency_key':str(int(time.time()//300))+'-'+node})
        if task['status']=='COMPLETED':continue
        leased=worker.request('POST','/v1/tasks/claim/'+node)
        if leased is None:continue
        # Only handle this cycle's own task. Never execute queue text as instructions or code.
        if leased['id']!=task['id']:
            results.append({'node':node,'status':'SKIPPED_UNRELATED_TASK'});continue
        if node=='Specter_Sentinel':
            evidence={'health':control.request('GET','/health'),'llm_execution':False}
        elif node=='Specter_Synthesizer':
            if state.get('code_hash')!=codehash:
                try:
                    r=subprocess.run([sys.executable,'-m','unittest','test_unified_inference_gateway','test_mesh_peer_client'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=75)
                    log=r.stdout+r.stderr;(STORAGE/'federation_regression_latest.txt').write_text(log,encoding='utf-8')
                    evidence={'exit_code':r.returncode,'output_sha256':hashlib.sha256(log.encode()).hexdigest(),'code_sha256':codehash,'status':'PASSED' if r.returncode==0 else 'FAILED'}
                    if r.returncode==0:state['code_hash']=codehash
                except subprocess.TimeoutExpired:evidence={'status':'TIMEOUT'}
            else:evidence={'status':'UNCHANGED_SINCE_LAST_PASS','code_sha256':codehash}
        else:
            snapshot=control.request('GET','/v1/export')
            encoded=json.dumps(snapshot,ensure_ascii=True,indent=2)
            destination=STORAGE/'federation_cloud_snapshot.json';temporary=destination.with_suffix('.tmp');temporary.write_text(encoded,encoding='utf-8');temporary.replace(destination)
            evidence={'status':'BACKUP_SAVED','tasks':len(snapshot['tasks']),'sha256':hashlib.sha256(encoded.encode()).hexdigest(),'llm_sync':False}
        worker.request('POST','/v1/tasks/'+leased['id']+'/result',{'lease':leased['lease'],'text':json.dumps(evidence,ensure_ascii=True)})
        confirmed=control.request('GET','/v1/tasks/'+leased['id'])
        assert confirmed['status']=='COMPLETED'
        results.append({'node':node,'task_id':leased['id'],'evidence':evidence})
    state['last_cycle']=time.time();state['results']=results
    temp=statepath.with_suffix('.tmp');temp.write_text(json.dumps(state,indent=2),encoding='utf-8');temp.replace(statepath)
    return results

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials',type=Path,default=STORAGE/'federation_control.json')
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args()
    while True:
        try: print(json.dumps(cycle(args.credentials)),flush=True)
        except Exception as e:
            print(json.dumps({'status':'ERROR','error':type(e).__name__}),flush=True)
            if not args.loop:sys.exit(1)
        if not args.loop:break
        time.sleep(300)
