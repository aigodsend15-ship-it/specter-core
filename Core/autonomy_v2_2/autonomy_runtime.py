from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from .agent_fabric import DurableAgentFabric, DEFAULT_ROLES
from .capacity_manager import CapacityManager, CapacityState
from .context_lineage import ContextLineageManager
from .research_miner import PublicResearchMiner
from .successor_chat import SuccessorChatManager
from .incident_guard import ProviderIncidentGuard
from .deliberation import DeliberationEngine
from .knowledge_core import KnowledgeCore
from .federation_hub import FederationHub
from .conversation_mesh import ConversationMesh
from .web_agent_worker import WebDispatchStore
from .trust_firewall import UntrustedContextFirewall

class SovereignAutonomyRuntime:
    """Composition root for durable autonomy. No provider quota bypass and no automatic spending/publication."""
    def __init__(self,root: str | Path):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.state_dir=self.root/'state'; self.state_dir.mkdir(exist_ok=True)
        self.lineage=ContextLineageManager(self.state_dir/'lineage.sqlite3')
        self.capacity=CapacityManager(self.state_dir/'capacity.sqlite3')
        self.fabric=DurableAgentFabric(self.state_dir/'fabric.sqlite3')
        self.research=PublicResearchMiner(self.state_dir/'research.sqlite3',allow_network=False)
        self.successor=SuccessorChatManager(self.lineage)
        self.incidents=ProviderIncidentGuard(self.state_dir/'provider_incidents.sqlite3')
        self.deliberation=DeliberationEngine(self.state_dir/'deliberation.sqlite3')
        self.knowledge=KnowledgeCore(self.state_dir/'knowledge.sqlite3')
        self.federation=FederationHub(self.state_dir/'federation.sqlite3')
        self.conversations=ConversationMesh(self.state_dir/'conversations.sqlite3')
        self.dispatches=WebDispatchStore(self.state_dir/'web_dispatches.sqlite3')
        self.firewall=UntrustedContextFirewall()
        self.pause_file=self.root/'PAUSE'; self.status_file=self.state_dir/'status.json'

    def close(self):
        for x in (self.lineage,self.capacity,self.fabric,self.research,self.incidents,self.deliberation,self.knowledge,self.federation,self.conversations,self.dispatches):
            try: x.close()
            except Exception: pass

    def paused(self) -> bool: return self.pause_file.exists()

    def register_endpoint(self,endpoint_id: str,provider: str,account_scope: str,quota_group: Optional[str]=None,concurrency: int=1):
        self.capacity.register(endpoint_id,provider,account_scope,quota_group,concurrency)

    def seed_mission(self,objective: str,project_id: str='specter-core') -> Dict[str,Any]:
        branch='main'; self.lineage.ensure_branch(project_id,branch)
        state={"objective":objective,"current_plan":["durable context lineage","ten-role agent fabric","quota-aware capacity","evidence-first research","verified releases"],"open_tasks":[],"artifacts":[],"recent_receipts":[],"invariants":{"owner_control":True,"quota_bypass":False,"auto_spend":False,"auto_publish":False},"next_action":"dispatch first evidence-gathering round"}
        cp=self.lineage.create_checkpoint(project_id,branch,state,event_seq=0)
        tasks=[]
        role_prompts={
          'ARCHITECT':'Review current architecture and propose one measurable improvement.',
          'WEB_ADAPTER':'Audit web LLM transport reliability and successor-chat support.',
          'LOCAL_EXECUTOR':'Audit local execution plane for typed tools, leases and receipts.',
          'SECURITY':'Threat-model the next autonomy increment and list fail-closed invariants.',
          'GITHUB_SCOUT':'Find public open-source patterns relevant to Codex-like agent runtimes.',
          'VERIFIER':'Define tests and evidence required before promotion.',
          'CONTEXT_LINEAGE':'Validate checkpoint, branch, merge and resume semantics.',
          'RELIABILITY':'Audit crash restart, idempotency and watchdog behavior.',
          'RELEASE_FUNDING':'Prepare release/funding materials without publishing or spending.'}
        for role,prompt in role_prompts.items(): tasks.append(self.fabric.submit(role,{"objective":objective,"instruction":prompt,"checkpoint_id":cp['checkpoint_id']},dedup_key=f"seed:{cp['checkpoint_id']}:{role}"))
        return {"checkpoint":cp,"tasks":tasks}

    def report_provider_text(self,endpoint_id: str,text: str,retry_after_s: Optional[int]=None) -> Dict[str,Any]:
        kind=self.incidents.classify_text(text)
        if not kind: return {"incident":None}
        qg=self.capacity.quota_group_for(endpoint_id)
        existing=self.incidents.get(qg)
        if existing and existing.blocked_until>time.time():
            return {"incident":kind,"quota_group":qg,"strikes":existing.strikes,"blocked_until":existing.blocked_until,"deduplicated":True}
        inc=self.incidents.record(qg,text,retry_after_s)
        self.capacity.report_group(qg,CapacityState.QUOTA_WAIT,health=0.5)
        return {"incident":kind,"quota_group":qg,"strikes":inc.strikes,"blocked_until":inc.blocked_until,"deduplicated":False}

    def report_provider_success(self,endpoint_id: str) -> Dict[str,Any]:
        qg=self.capacity.quota_group_for(endpoint_id); self.incidents.clear(qg)
        self.capacity.report_group(qg,CapacityState.QUOTA_WAIT)
        self.capacity.report(endpoint_id,CapacityState.AVAILABLE,health=1.0,active=0)
        return {"quota_group":qg,"probe_endpoint":endpoint_id,"state":"AVAILABLE"}

    def tick(self) -> Dict[str,Any]:
        if self.paused():
            status={"ts":time.time(),"state":"PAUSED","fabric":self.fabric.summary(),"capacity":self.capacity.summary()}
            self.status_file.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8'); return status
        expired=self.fabric.reconcile_expired()
        probes=[]
        for qg in self.incidents.due_groups():
            endpoint=self.capacity.arm_single_probe(qg)
            if endpoint: probes.append({"quota_group":qg,"endpoint_id":endpoint})
        available=self.capacity.select(count=10)
        # Bind available reasoning endpoints opportunistically. Missing capacity is not failure.
        for role in DEFAULT_ROLES:
            if role=='COORDINATOR': self.fabric.bind_endpoint(role,'local:coordinator','IDLE'); continue
            idx=list(DEFAULT_ROLES).index(role)-1
            if idx < len(available): self.fabric.bind_endpoint(role,available[idx].endpoint_id,'IDLE')
            else: self.fabric.bind_endpoint(role,None,'WAITING_MODEL')
        cap=self.capacity.summary(); fabric=self.fabric.summary()
        state='RUNNING' if cap['available_slots'] else 'RUNNING_LOCAL_WAITING_MODEL'
        status={"ts":time.time(),"version":"2.2.0","state":state,"expired_reconciled":expired,"quota_probes_armed":probes,"fabric":fabric,"capacity":cap,"provider_incidents":self.incidents.summary(),"knowledge":self.knowledge.stats(),"federation":self.federation.summary(),"conversation_mesh":self.conversations.all(),"invariants":{"quota_bypass":False,"account_rotation_for_quota":False,"auto_spend":False,"auto_publish":False,"authority_external":True,"data_never_grants_authority":True}}
        self.status_file.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
        return status

    def run(self,interval_s: float=10.0):
        while True:
            self.tick(); time.sleep(max(1.0,float(interval_s)))
