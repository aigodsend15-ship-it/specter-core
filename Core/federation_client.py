"""Authenticated HTTP adapter usable by OpenCode, Hermes and other HTTP agents."""
import argparse,json,os,urllib.request
from pathlib import Path
class FederationClient:
    def __init__(self,endpoint=None,token=None):
        self.endpoint=(endpoint or os.getenv('SPECTER_MESH_ENDPOINT','https://pintograndao-hermes-bridge.hf.space')).rstrip('/')
        self.token=token or os.getenv('SPECTER_AUTH_TOKEN','')
    def request(self,method,path,data=None):
        headers={'Content-Type':'application/json'}
        if self.token: headers['Authorization']='Bearer '+self.token
        request=urllib.request.Request(self.endpoint+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
        with urllib.request.urlopen(request,timeout=30) as response:
            return None if response.status==204 else json.load(response)
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['health','declare-node','nodes','register','claim','result','submit','task','export'])
    parser.add_argument('--node',default=os.getenv('SPECTER_NODE_ID'))
    parser.add_argument('--task-id');parser.add_argument('--text');parser.add_argument('--lease');parser.add_argument('--idempotency-key')
    parser.add_argument('--endpoint');parser.add_argument('--role',default='independent-collaborator');parser.add_argument('--capability',action='append',default=[])
    parser.add_argument('--credentials',type=Path);parser.add_argument('--control',action='store_true')
    args=parser.parse_args(); client=FederationClient()
    if args.credentials:
        config=json.loads(args.credentials.read_text(encoding='utf-8'));client=FederationClient(config['endpoint'],config['control_key' if args.control else 'worker_key'])
    if args.action=='health':result=client.request('GET','/health')
    elif args.action=='declare-node':
        if not args.node or not args.endpoint:parser.error('--node and --endpoint required')
        result=client.request('POST','/v1/public-nodes',{'id':args.node,'role':args.role,'endpoint':args.endpoint,'capabilities':args.capability})
    elif args.action=='nodes':result=client.request('GET','/v1/public-nodes')
    elif args.action=='register':
        if not args.node:parser.error('--node required')
        result=client.request('POST','/v1/agents',{'id':args.node,'role':'external-collaborator'})
    elif args.action=='claim':
        if not args.node:parser.error('--node required')
        result=client.request('POST','/v1/tasks/claim/'+args.node)
    elif args.action=='result':
        if not args.task_id or not args.lease or args.text is None:parser.error('--task-id, --lease and --text required')
        result=client.request('POST','/v1/tasks/'+args.task_id+'/result',{'lease':args.lease,'text':args.text})
    elif args.action=='submit':
        if not args.node or not args.text or not args.idempotency_key:parser.error('--node, --text and --idempotency-key required')
        result=client.request('POST','/v1/tasks',{'target':args.node,'prompt':args.text,'idempotency_key':args.idempotency_key})
    elif args.action=='task':
        if not args.task_id:parser.error('--task-id required')
        result=client.request('GET','/v1/tasks/'+args.task_id)
    else:result=client.request('GET','/v1/export')
    print(json.dumps(result,indent=2,ensure_ascii=True))
