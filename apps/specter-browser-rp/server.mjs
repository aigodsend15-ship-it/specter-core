import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { acceptWebSocket, WS_OPEN } from './src/websocket.mjs';
import { WorldStore } from './src/persistence.mjs';

const __dirname=path.dirname(fileURLToPath(import.meta.url));
const PUBLIC=path.join(__dirname,'public');
const VENDOR=path.join(__dirname,'node_modules');
const PORT=Number(process.env.PORT||7860);
const DATA=process.env.DATA_DIR?path.resolve(process.env.DATA_DIR):path.join(__dirname,'data');
const VERSION='0.3.0-worldmind';
const store=new WorldStore(path.join(DATA,'world.db'));
const peers=new Map();
const npcs=store.getNpcs();
let builds=store.getBuilds();
let worldTime=Number(store.getMeta('worldTime')||9);
let npcBuildSequence=store.getBuildCount();
let lastPersist=Date.now();
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.wasm':'application/wasm','.json':'application/json','.glb':'model/gltf-binary','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'};
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const safeName=v=>String(v??'Player').replace(/[^\p{L}\p{N}_ -]/gu,'').trim().slice(0,24)||'Player';
const safeText=v=>String(v??'').replace(/[\u0000-\u001f]/g,' ').trim().slice(0,240);
const round=(v,n=2)=>Number(Number(v).toFixed(n));
function send(ws,o){if(ws.readyState===WS_OPEN)ws.send(JSON.stringify(o));}
function broadcast(o){const s=JSON.stringify(o);for(const p of peers.values())if(p.ws.readyState===WS_OPEN)p.ws.send(s);}
function pubPlayer(p){return{id:p.id,name:p.name,x:round(p.x),y:round(p.y),z:round(p.z),yaw:round(p.yaw,3),money:p.money,mode:p.mode};}
function pubNpc(n){return{id:n.id,name:n.name,x:round(n.x),z:round(n.z),yaw:round(n.yaw,3),energy:Math.round(n.energy),materials:round(n.materials,1),role:n.role,goal:n.goal};}
function pubBuild(b){return{id:b.id,owner:b.owner,kind:b.kind,x:b.x,y:b.y,z:b.z,yaw:b.yaw};}

function serveFile(res,root,pathname){
  const rel=decodeURIComponent(pathname).replace(/^\/+/, '');
  const file=path.resolve(root,rel);
  if(!file.startsWith(path.resolve(root)+path.sep)&&file!==path.resolve(root)){res.writeHead(403);res.end('forbidden');return;}
  fs.readFile(file,(e,d)=>{if(e){res.writeHead(404);res.end('not found');return;}res.writeHead(200,{'content-type':mime[path.extname(file)]||'application/octet-stream','cache-control':pathname.includes('/vendor/')?'public,max-age=86400':'no-cache','cross-origin-resource-policy':'cross-origin'});res.end(d);});
}
const server=http.createServer((req,res)=>{
  const u=new URL(req.url,'http://localhost');
  if(u.pathname==='/health'){res.writeHead(200,{'content-type':'application/json'});return res.end(JSON.stringify({ok:true,version:VERSION,players:peers.size,npcs:npcs.length,builds:builds.length,uptime:round(process.uptime(),1)}));}
  if(u.pathname==='/world'){res.writeHead(200,{'content-type':'application/json'});return res.end(JSON.stringify({version:VERSION,time:worldTime,npcs:npcs.map(pubNpc),builds:builds.map(pubBuild)}));}
  if(u.pathname.startsWith('/vendor/'))return serveFile(res,VENDOR,u.pathname.slice('/vendor/'.length));
  return serveFile(res,PUBLIC,u.pathname==='/'?'index.html':u.pathname);
});

function nearestNpc(p,id){const n=id?npcs.find(x=>x.id===id):null;if(n)return n;let best=null,bd=Infinity;for(const x of npcs){const d=Math.hypot(x.x-p.x,x.z-p.z);if(d<bd){bd=d;best=x}}return bd<=12?best:null;}
function deterministicReply(npc,text){
  const roleLine={builder:'Estou juntando material para expandir este quarteirão.',mechanic:'Estou de olho nos carros. Essa cidade precisa de uma oficina melhor.',explorer:'Tenho mapeado ruas e atalhos. Ainda há muito espaço vazio.',trader:'Se a cidade crescer, quero abrir um mercado aqui.',artist:'Quero transformar esses muros em um lugar que tenha identidade.',runner:'Estou testando rotas rápidas entre os bairros.',gardener:'Estou procurando um terreno para deixar esta parte mais verde.'}[npc.role]||'Estou tentando descobrir o que fazer a seguir.';
  const low=text.toLowerCase();
  if(low.includes('constru')||low.includes('build'))return `${roleLine} Tenho ${Math.floor(npc.materials)} unidades de material agora.`;
  if(low.includes('quem')||low.includes('nome'))return `Sou ${npc.name}. Trabalho como ${npc.role} e meu objetivo atual é ${npc.goal}.`;
  if(low.includes('ajud'))return `Pode explorar e construir com B. Eu vou lembrar do que acontece por aqui.`;
  return `${roleLine} ${npc.memory.split('|').at(-1)?.trim()||''}`.trim();
}
async function npcReply(npc,text){
  const base=process.env.NPC_AI_BASE_URL?.replace(/\/$/,'');
  const model=process.env.NPC_AI_MODEL;
  if(!base||!model)return deterministicReply(npc,text);
  const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),4500);
  try{
    const headers={'content-type':'application/json'};if(process.env.NPC_AI_API_KEY)headers.authorization=`Bearer ${process.env.NPC_AI_API_KEY}`;
    const r=await fetch(`${base}/chat/completions`,{method:'POST',headers,signal:ctrl.signal,body:JSON.stringify({model,temperature:.7,max_tokens:90,messages:[{role:'system',content:`Você é ${npc.name}, NPC persistente de West Coast. Papel: ${npc.role}. Objetivo: ${npc.goal}. Memória: ${npc.memory}. Responda em português, 1-2 frases, permaneça no mundo do jogo.`},{role:'user',content:text}]})});
    if(!r.ok)throw new Error(`AI ${r.status}`);const j=await r.json();return safeText(j?.choices?.[0]?.message?.content)||deterministicReply(npc,text);
  }catch{return deterministicReply(npc,text)}finally{clearTimeout(timer)}
}
function appendMemory(npc,event){const bits=(npc.memory?npc.memory.split('|'):[]).map(x=>x.trim()).filter(Boolean);bits.push(event);npc.memory=bits.slice(-8).join(' | ').slice(-700);}
function canBuildAt(x,z){
  if(Math.abs(x)>210||Math.abs(z)>210)return false;
  for(const b of builds)if(Math.hypot(b.x-x,b.z-z)<3.4)return false;
  return true;
}
function createBuild(owner,kind,x,z,yaw=0){
  if(builds.length>=220||!canBuildAt(x,z))return null;
  const b={id:`build-${Date.now().toString(36)}-${(++npcBuildSequence).toString(36)}`,owner,kind:['crate','wall','bench','planter'].includes(kind)?kind:'crate',x:round(x),y:0,z:round(z),yaw:round(yaw,3),created_at:Date.now()};
  builds.push(b);store.addBuild(b);return b;
}

server.on('upgrade',(req,socket,head)=>{
  const ws=acceptWebSocket(req,socket,head,{path:'/ws'});if(!ws)return;
  const p={ws,id:crypto.randomUUID(),token:null,name:'Player',x:0,y:1.2,z:0,yaw:0,money:500,mode:'foot',ready:false,lastState:0};peers.set(p.id,p);
  ws.on('message',async buf=>{
    let m;try{m=JSON.parse(buf.toString())}catch{return}if(!m||typeof m.type!=='string')return;
    if(m.type==='hello'&&!p.ready){
      p.token=typeof m.token==='string'&&m.token.length>10?m.token:crypto.randomUUID();const saved=store.getPlayer(p.token);
      if(saved)Object.assign(p,{name:saved.name,x:saved.x,y:saved.y,z:saved.z,yaw:saved.yaw,money:saved.money,mode:saved.mode||'foot'});else p.name=safeName(m.name);
      p.ready=true;send(ws,{type:'welcome',self:pubPlayer(p),token:p.token,version:VERSION,world:{time:worldTime,npcs:npcs.map(pubNpc),builds:builds.map(pubBuild)}});broadcast({type:'system',text:`${p.name} entrou em West Coast.`});return;
    }
    if(!p.ready)return;
    if(m.type==='state'){
      const nx=clamp(Number(m.x)||0,-218,218),ny=clamp(Number(m.y)||1.2,-2,60),nz=clamp(Number(m.z)||0,-218,218);const now=Date.now(),dt=Math.max(.05,Math.min(1,(now-(p.lastState||now-100))/1000));const max=(m.mode==='car'?62:14)*dt+3;const dist=Math.hypot(nx-p.x,nz-p.z);if(dist<=max||!p.lastState){p.x=nx;p.y=ny;p.z=nz;}p.lastState=now;if(Number.isFinite(m.yaw))p.yaw=Number(m.yaw);if(m.mode==='foot'||m.mode==='car')p.mode=m.mode;
    }else if(m.type==='build'){
      if(p.mode!=='foot')return;const x=Number(m.x),z=Number(m.z);if(!Number.isFinite(x)||!Number.isFinite(z)||Math.hypot(x-p.x,z-p.z)>8)return;const b=createBuild(p.name,m.kind,x,z,Number(m.yaw)||0);if(b){broadcast({type:'build_added',build:pubBuild(b)});send(ws,{type:'system',text:`Construção persistida: ${b.kind}.`})}
    }else if(m.type==='talk_npc'){
      const npc=nearestNpc(p,m.id);if(!npc)return send(ws,{type:'system',text:'Nenhum NPC perto o bastante.'});const text=safeText(m.text)||'Olá. O que você está fazendo?';appendMemory(npc,`${p.name} perguntou: ${text.slice(0,80)}`);const reply=await npcReply(npc,text);appendMemory(npc,`${npc.name} respondeu: ${reply.slice(0,100)}`);store.putNpc(npc);send(ws,{type:'npc_chat',id:npc.id,name:npc.name,text:reply});
    }else if(m.type==='chat'){
      const text=safeText(m.text);if(!text)return;if(text==='/save'){store.putPlayer(p);return send(ws,{type:'system',text:'Estado salvo.'})}broadcast({type:'chat',name:p.name,text});
    }
  });
  ws.on('close',()=>{peers.delete(p.id);if(p.ready&&p.token)store.putPlayer(p)});ws.on('error',()=>{});
});

function tickNpcs(dt,now){
  for(let i=0;i<npcs.length;i++){
    const n=npcs[i];n.energy=clamp(n.energy-dt*.18,0,100);
    if(n.energy<18){n.goal='rest';n.energy=clamp(n.energy+dt*2.4,0,100);continue;}
    if(n.materials<8)n.goal='gather';else if(n.role==='builder'||n.materials>14)n.goal='build';else n.goal='explore';
    const phase=(now/1000)*(0.22+(i%4)*.025)+i*1.7;
    let tx=Math.sin(phase*.31+i)*150,tz=Math.cos(phase*.27-i)*150;
    if(n.goal==='gather'){tx=Math.round(tx/50)*50;tz=Math.round(tz/50)*50;n.materials=Math.min(30,n.materials+dt*.28);n.credits+=Math.random()<dt*.03?1:0;}
    if(n.goal==='build'&&n.materials>=8&&now>(n.nextBuildAt||0)){
      const bx=clamp(Math.round((n.x+Math.sin(i)*9)/4)*4,-198,198),bz=clamp(Math.round((n.z+Math.cos(i)*9)/4)*4,-198,198);const b=createBuild(n.name,['crate','wall','bench','planter'][i%4],bx,bz,n.yaw);n.nextBuildAt=now+45000+i*1700;if(b){n.materials-=8;appendMemory(n,`Construiu ${b.kind} em ${b.x},${b.z}`);broadcast({type:'build_added',build:pubBuild(b)})}n.goal='explore';
    }
    const dx=tx-n.x,dz=tz-n.z,d=Math.hypot(dx,dz);if(d>2){const sp=n.goal==='gather'?1.5:1.1;n.x=clamp(n.x+dx/d*sp*dt,-205,205);n.z=clamp(n.z+dz/d*sp*dt,-205,205);n.yaw=Math.atan2(dx,dz);}
  }
}
setInterval(()=>{
  const now=Date.now(),dt=.25;worldTime=(worldTime+dt*.0065)%24;tickNpcs(dt,now);
  if(now-lastPersist>5000){for(const n of npcs)store.putNpc(n);store.setMeta('worldTime',worldTime);for(const p of peers.values())if(p.ready&&p.token)store.putPlayer(p);lastPersist=now;}
},250);
setInterval(()=>{const players=[...peers.values()].filter(p=>p.ready).map(pubPlayer);if(players.length)broadcast({type:'snapshot',players,npcs:npcs.map(pubNpc),time:round(worldTime,2)});},200);

function shutdown(){for(const p of peers.values())if(p.ready&&p.token)store.putPlayer(p);for(const n of npcs)store.putNpc(n);store.setMeta('worldTime',worldTime);server.close(()=>{store.close();process.exit(0)});setTimeout(()=>process.exit(1),3000).unref();}
process.on('SIGTERM',shutdown);process.on('SIGINT',shutdown);
server.listen(PORT,'0.0.0.0',()=>console.log(JSON.stringify({event:'server_started',version:VERSION,port:PORT,npcs:npcs.length,builds:builds.length})));
