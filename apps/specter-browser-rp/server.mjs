import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { acceptWebSocket, WS_OPEN } from './src/websocket.mjs';
import { PlayerStore } from './src/persistence.mjs';

const __dirname=path.dirname(fileURLToPath(import.meta.url));
const PUBLIC=path.join(__dirname,'public');
const PORT=Number(process.env.PORT||7860);
const DATA=process.env.DATA_DIR?path.resolve(process.env.DATA_DIR):path.join(__dirname,'data');
const store=new PlayerStore(path.join(DATA,'players.db'));
const peers=new Map();
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.wasm':'application/wasm'};
const safeName=v=>String(v??'Player').replace(/[^\p{L}\p{N}_ -]/gu,'').trim().slice(0,24)||'Player';
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
function send(ws,o){if(ws.readyState===WS_OPEN)ws.send(JSON.stringify(o));}
function broadcast(o){const s=JSON.stringify(o);for(const p of peers.values())if(p.ws.readyState===WS_OPEN)p.ws.send(s);}
function pub(p){return{id:p.id,name:p.name,x:p.x,z:p.z,yaw:p.yaw,money:p.money,mode:p.mode};}

const server=http.createServer((req,res)=>{
  if(req.url==='/health'){res.writeHead(200,{'content-type':'application/json'});return res.end(JSON.stringify({ok:true,players:peers.size,uptime:process.uptime()}));}
  const u=new URL(req.url,'http://localhost');let rel=decodeURIComponent(u.pathname==='/'?'/index.html':u.pathname);rel=path.normalize(rel).replace(/^([.][.][/\\])+/,'');const file=path.join(PUBLIC,rel);
  if(!file.startsWith(PUBLIC)){res.writeHead(403);return res.end('forbidden');}
  fs.readFile(file,(e,d)=>{if(e){res.writeHead(404);return res.end('not found');}res.writeHead(200,{'content-type':mime[path.extname(file)]||'application/octet-stream','cache-control':'no-cache'});res.end(d);});
});

server.on('upgrade',(req,socket,head)=>{
  const ws=acceptWebSocket(req,socket,head,{path:'/ws'});if(!ws)return;
  const p={ws,id:crypto.randomUUID(),token:null,name:'Player',x:0,z:0,yaw:0,money:500,mode:'foot',f:0,r:0,sprint:false,ready:false};peers.set(p.id,p);
  ws.on('message',buf=>{let m;try{m=JSON.parse(buf.toString())}catch{return}if(!m||typeof m.type!=='string')return;
    if(m.type==='hello'&&!p.ready){p.token=typeof m.token==='string'&&m.token.length>10?m.token:crypto.randomUUID();const saved=store.get(p.token);if(saved)Object.assign(p,{name:saved.name,x:saved.x,z:saved.z,yaw:saved.yaw,money:saved.money});else p.name=safeName(m.name);p.ready=true;send(ws,{type:'welcome',self:pub(p),token:p.token});broadcast({type:'system',text:`${p.name} entrou.`});return;}
    if(!p.ready)return;
    if(m.type==='input'){p.f=clamp(Number(m.f)||0,-1,1);p.r=clamp(Number(m.r)||0,-1,1);p.sprint=!!m.sprint;if(Number.isFinite(m.yaw))p.yaw=m.yaw;}
    else if(m.type==='mode'&&(m.mode==='foot'||m.mode==='car'))p.mode=m.mode;
    else if(m.type==='chat'){const text=String(m.text??'').trim().slice(0,180);if(!text)return;if(text==='/car'){p.mode=p.mode==='car'?'foot':'car';return send(ws,{type:'system',text:`Modo: ${p.mode}`});}if(text==='/save'){store.put(p);return send(ws,{type:'system',text:'Salvo.'});}broadcast({type:'chat',name:p.name,text});}
  });
  ws.on('close',()=>{peers.delete(p.id);if(p.ready&&p.token)store.put(p);});
});

setInterval(()=>{for(const p of peers.values()){if(!p.ready)continue;const dt=.05,speed=p.mode==='car'?18:(p.sprint?7:4.2),s=Math.sin(p.yaw),c=Math.cos(p.yaw);p.x=clamp(p.x+(s*p.f+c*p.r)*speed*dt,-240,240);p.z=clamp(p.z+(c*p.f-s*p.r)*speed*dt,-240,240);}},50);
setInterval(()=>{const players=[...peers.values()].filter(p=>p.ready).map(pub);if(players.length)broadcast({type:'snapshot',players});},100);
process.on('SIGTERM',()=>{for(const p of peers.values())if(p.ready&&p.token)store.put(p);server.close(()=>{store.close();process.exit(0)});});
server.listen(PORT,'0.0.0.0',()=>console.log(JSON.stringify({event:'server_started',port:PORT})));
