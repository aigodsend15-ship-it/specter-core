"""Hourly bounded free-model review, submitted to the federation as a proposal."""
import hashlib,json,subprocess,time,sys
from pathlib import Path
from federation_client import FederationClient
ROOT=Path(__file__).resolve().parent
STORAGE=ROOT/'storage'
EXE=ROOT.parent/'tools/opencode/node_modules/opencode-ai/bin/opencode.exe'
PROJECT=ROOT.parent/'opencode-federation'
def run():
    keys=json.loads((STORAGE/'federation_control.json').read_text(encoding='utf-8'))
    control=FederationClient(keys['endpoint'],keys['control_key']);worker=FederationClient(keys['endpoint'],keys['worker_key'])
    slot=int(time.time()//3600);statefile=STORAGE/'opencode_cycle_state.json'
    if statefile.exists() and json.loads(statefile.read_text())['slot']==slot:return
    node='OpenCode_Coordinator';worker.request('POST','/v1/agents',{'id':node,'role':'bounded-free-model-review'})
    prompt='Planeje UMA melhoria pequena para uma API de colaboração. Há Control autenticado, fila SQLite, reservas de 120 segundos, exportação para backup no PC a cada 5 minutos e restauração apenas em fila vazia, com reservas antigas invalidadas. O Space pode suspender; o PC pode estar desligado. Chame specter-planner e specter-reviewer uma vez cada. Proponha um patch conceitual e teste de aceitação, sem alegar execução. Não use ferramentas além desses dois subagentes.'
    task=control.request('POST','/v1/tasks',{'target':node,'prompt':prompt,'idempotency_key':'opencode-hour-'+str(slot)})
    if task['status']=='COMPLETED':return
    leased=worker.request('POST','/v1/tasks/claim/'+node)
    if not leased or leased['id']!=task['id']:return
    # Mark attempt before inference: provider failures do not cause an immediate retry loop.
    statefile.write_text(json.dumps({'slot':slot,'status':'STARTED','task_id':task['id']}))
    logpath=STORAGE/'opencode_latest.jsonl'
    try:
        with logpath.open('w',encoding='utf-8') as output:
            proc=subprocess.run([str(EXE),'run','--agent','specter-coordinator','--format','json',prompt],cwd=PROJECT,stdout=output,stderr=subprocess.STDOUT,timeout=100,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        events=[]
        for line in logpath.read_text(encoding='utf-8-sig').splitlines():
            try:events.append(json.loads(line))
            except ValueError:pass
        texts=[e['part']['text'] for e in events if e.get('type')=='text']
        calls=[e['part']['state']['input'].get('subagent_type') for e in events if e.get('type')=='tool_use' and e['part'].get('tool')=='task' and e['part']['state'].get('status')=='completed']
        evidence={'status':'PROPOSAL' if proc.returncode==0 and texts else 'FAILED','model':'opencode/mimo-v2.5-free','subagents':calls,'proposal':'\n'.join(texts)[-24000:],'reported_parent_cost':sum(e['part'].get('cost',0) for e in events if e.get('type')=='step_finish'),'automatic_deployment':False,'log_sha256':hashlib.sha256(logpath.read_bytes()).hexdigest()}
    except subprocess.TimeoutExpired:evidence={'status':'TIMEOUT','automatic_deployment':False}
    worker.request('POST','/v1/tasks/'+task['id']+'/result',{'lease':leased['lease'],'text':json.dumps(evidence,ensure_ascii=True)})
    statefile.write_text(json.dumps({'slot':slot,'status':evidence['status'],'task_id':task['id']},indent=2))
    print(json.dumps({'task_id':task['id'],'status':evidence['status'],'subagents':evidence.get('subagents',[])}))
if __name__=='__main__':
    try:run()
    except Exception as e:
        (STORAGE/'opencode_cycle_error.json').write_text(json.dumps({'at':time.time(),'error':type(e).__name__}))
        sys.exit(1)
