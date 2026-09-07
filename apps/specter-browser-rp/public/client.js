import { GTAInstall, LEGAL_NOTE } from './gtasa-loader.js';

const $=s=>document.querySelector(s);
const canvas=$('#game');
const gl=canvas.getContext('webgl2',{antialias:true,alpha:false,powerPreference:'high-performance'});
if(!gl) throw new Error('WebGL2 não suportado neste navegador.');

const ui={
  launcher:$('#launcher'), assetBtn:$('#assetBtn'), assetStatus:$('#assetStatus'),
  progressBox:$('#progressBox'), progressBar:$('#progressBar'), readyBox:$('#readyBox'),
  assetStats:$('#assetStats'), errorBox:$('#errorBox'), joinBtn:$('#joinBtn'), name:$('#name'),
  hud:$('#hud'), net:$('#net'), stats:$('#stats'), hint:$('#hint'), chat:$('#chat'),
  messages:$('#messages'), chatInput:$('#chatInput')
};

const SPAWN=[2495,14,-1685];
const WORLD_RADIUS=850;
let install=null, worldReady=false, worldInstances=[], meshCache=new Map();
let ws=null,self=null,token=localStorage.specterToken||null,yaw=0.2,pitch=0.24,mode='foot';
const players=new Map(), keys=new Set();

function setProgress(text,p){
  ui.progressBox.classList.remove('hidden');
  ui.assetStatus.textContent=text;
  if(Number.isFinite(p)) ui.progressBar.style.width=`${Math.max(0,Math.min(1,p))*100}%`;
}
function fail(e){
  console.error(e);
  ui.errorBox.textContent=`${e?.message||e}`;
  ui.errorBox.classList.remove('hidden');
  ui.assetBtn.disabled=false;
}
function msg(t){
  const d=document.createElement('div');d.className='msg';d.textContent=t;
  ui.messages.append(d);while(ui.messages.children.length>9)ui.messages.firstChild.remove();
}

function shader(type,src){
  const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);
  if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));
  return s;
}
const vs=`#version 300 es
precision highp float;
layout(location=0) in vec3 p;
layout(location=1) in vec3 n;
uniform mat4 vp;
uniform mat4 model;
out vec3 N;
out vec3 W;
void main(){
  vec4 w=model*vec4(p,1.0);
  W=w.xyz;
  N=normalize(mat3(model)*n);
  gl_Position=vp*w;
}`;
const fs=`#version 300 es
precision highp float;
in vec3 N;
in vec3 W;
uniform vec3 baseColor;
uniform vec3 eye;
out vec4 outColor;
void main(){
  vec3 sun=normalize(vec3(-0.35,0.82,0.28));
  float nd=max(dot(normalize(N),sun),0.0);
  float light=0.42+nd*0.58;
  vec3 c=baseColor*light;
  float dist=distance(W,eye);
  float fog=smoothstep(470.0,850.0,dist);
  c=mix(c,vec3(0.48,0.68,0.82),fog);
  outColor=vec4(c,1.0);
}`;
const prog=gl.createProgram();
gl.attachShader(prog,shader(gl.VERTEX_SHADER,vs));
gl.attachShader(prog,shader(gl.FRAGMENT_SHADER,fs));
gl.linkProgram(prog);
if(!gl.getProgramParameter(prog,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(prog));
const U={
  vp:gl.getUniformLocation(prog,'vp'), model:gl.getUniformLocation(prog,'model'),
  color:gl.getUniformLocation(prog,'baseColor'), eye:gl.getUniformLocation(prog,'eye')
};

function normalize3(a){
  const l=Math.hypot(a[0],a[1],a[2])||1;return[a[0]/l,a[1]/l,a[2]/l];
}
function sub3(a,b){return[a[0]-b[0],a[1]-b[1],a[2]-b[2]]}
function cross3(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}
function dot3(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]}

function perspective(fovy,aspect,near,far){
  const f=1/Math.tan(fovy/2),nf=1/(near-far);
  return new Float32Array([
    f/aspect,0,0,0,
    0,f,0,0,
    0,0,(far+near)*nf,-1,
    0,0,2*far*near*nf,0
  ]);
}
function lookAt(eye,center,up=[0,1,0]){
  const z=normalize3(sub3(eye,center));
  const x=normalize3(cross3(up,z));
  const y=cross3(z,x);
  return new Float32Array([
    x[0],y[0],z[0],0,
    x[1],y[1],z[1],0,
    x[2],y[2],z[2],0,
    -dot3(x,eye),-dot3(y,eye),-dot3(z,eye),1
  ]);
}
function mul(a,b){
  const r=new Float32Array(16);
  for(let c=0;c<4;c++)for(let row=0;row<4;row++)
    r[c*4+row]=a[row]*b[c*4]+a[4+row]*b[c*4+1]+a[8+row]*b[c*4+2]+a[12+row]*b[c*4+3];
  return r;
}
function modelMatrix(q,p,scale=[1,1,1]){
  let [x,y,z,w]=q;
  const l=Math.hypot(x,y,z,w)||1;x/=l;y/=l;z/=l;w/=l;
  const x2=x+x,y2=y+y,z2=z+z;
  const xx=x*x2,xy=x*y2,xz=x*z2,yy=y*y2,yz=y*z2,zz=z*z2;
  const wx=w*x2,wy=w*y2,wz=w*z2;
  return new Float32Array([
    (1-(yy+zz))*scale[0],(xy+wz)*scale[0],(xz-wy)*scale[0],0,
    (xy-wz)*scale[1],(1-(xx+zz))*scale[1],(yz+wx)*scale[1],0,
    (xz+wy)*scale[2],(yz-wx)*scale[2],(1-(xx+yy))*scale[2],0,
    p[0],p[1],p[2],1
  ]);
}
function identityAt(p,scale=[1,1,1]){return modelMatrix([0,0,0,1],p,scale)}

function hashColor(s){
  let h=2166136261;
  for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}
  const r=0.42+((h>>>0)&255)/255*0.28;
  const g=0.40+((h>>>8)&255)/255*0.28;
  const b=0.38+((h>>>16)&255)/255*0.25;
  return [r,g,b];
}

function computeNormals(v,ind){
  const n=new Float32Array(v.length);
  for(let i=0;i+2<ind.length;i+=3){
    const ia=ind[i]*3,ib=ind[i+1]*3,ic=ind[i+2]*3;
    const ax=v[ia],ay=v[ia+1],az=v[ia+2];
    const bx=v[ib],by=v[ib+1],bz=v[ib+2];
    const cx=v[ic],cy=v[ic+1],cz=v[ic+2];
    const abx=bx-ax,aby=by-ay,abz=bz-az,acx=cx-ax,acy=cy-ay,acz=cz-az;
    const nx=aby*acz-abz*acy,ny=abz*acx-abx*acz,nz=abx*acy-aby*acx;
    for(const j of[ia,ib,ic]){n[j]+=nx;n[j+1]+=ny;n[j+2]+=nz}
  }
  for(let i=0;i<n.length;i+=3){
    const l=Math.hypot(n[i],n[i+1],n[i+2])||1;n[i]/=l;n[i+1]/=l;n[i+2]/=l;
  }
  return n;
}
function makeMesh(data){
  const vao=gl.createVertexArray();gl.bindVertexArray(vao);
  const vb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vb);gl.bufferData(gl.ARRAY_BUFFER,data.vertices,gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,0,0);
  const normals=computeNormals(data.vertices,data.indices);
  const nb=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,nb);gl.bufferData(gl.ARRAY_BUFFER,normals,gl.STATIC_DRAW);
  gl.enableVertexAttribArray(1);gl.vertexAttribPointer(1,3,gl.FLOAT,false,0,0);
  const ib=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,ib);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,data.indices,gl.STATIC_DRAW);
  gl.bindVertexArray(null);
  return {vao,count:data.indices.length,type:data.indices instanceof Uint32Array?gl.UNSIGNED_INT:gl.UNSIGNED_SHORT};
}

function cubeMesh(){
  const v=new Float32Array([
    -1,-1,-1, 1,-1,-1, 1,1,-1,-1,1,-1,
    -1,-1,1, 1,-1,1, 1,1,1,-1,1,1
  ]);
  const i=new Uint16Array([
    0,2,1,0,3,2,4,5,6,4,6,7,0,4,7,0,7,3,
    1,2,6,1,6,5,3,7,6,3,6,2,0,1,5,0,5,4
  ]);
  return makeMesh({vertices:v,indices:i});
}
const playerMesh=cubeMesh();

async function prepareWorld(){
  const placements=install.nearby(SPAWN,WORLD_RADIUS,1500);
  if(!placements.length) throw new Error('A instalação foi reconhecida, mas nenhum placement externo foi encontrado perto de Los Santos.');
  const names=[];
  const seen=new Set();
  for(const p of placements) if(!seen.has(p.model)){seen.add(p.model);names.push(p.model)}
  const selectedNames=names.slice(0,260);
  const selected=new Set(selectedNames);
  let next=0,ok=0,failed=0;
  async function worker(){
    while(true){
      const idx=next++;if(idx>=selectedNames.length)return;
      const name=selectedNames[idx];
      try{
        const raw=await install.model(name);
        if(raw.vertices.length/3>300000 || raw.indices.length>900000) throw new Error('mesh grande demais');
        meshCache.set(name,makeMesh(raw));ok++;
      }catch(e){failed++;console.debug('DFF skip',name,e)}
      if((idx&7)===0){
        setProgress(`Convertendo DFF: ${Math.min(idx+1,selectedNames.length)}/${selectedNames.length} · ${ok} modelos prontos`,0.64+0.30*((idx+1)/selectedNames.length));
        await new Promise(r=>setTimeout(r,0));
      }
    }
  }
  await Promise.all(Array.from({length:Math.min(6,selectedNames.length)},()=>worker()));
  worldInstances=placements.filter(p=>selected.has(p.model)&&meshCache.has(p.model)).map(p=>({
    mesh:meshCache.get(p.model),
    model:p.model,
    matrix:modelMatrix(p.rotation,p.position),
    position:p.position,
    color:hashColor(p.model)
  }));
  if(worldInstances.length<20) throw new Error(`Poucos DFFs puderam ser convertidos (${worldInstances.length} instâncias). Esta edição do GTA:SA pode usar um formato ainda não suportado.`);
  worldReady=true;
  setProgress(`Los Santos pronto: ${worldInstances.length} instâncias usando ${meshCache.size} modelos DFF reais`,1);
  ui.assetStats.textContent=`${install.img.entries.length.toLocaleString()} entradas IMG · ${install.defs.size.toLocaleString()} definições · ${install.placements.length.toLocaleString()} placements · ${worldInstances.length.toLocaleString()} objetos carregados`;
  ui.readyBox.classList.remove('hidden');
}

async function loadInstall(candidate){
  install=candidate;ui.assetBtn.disabled=true;ui.errorBox.classList.add('hidden');
  await install.initialize(setProgress);
  await prepareWorld();
  ui.assetBtn.disabled=false;
}

ui.assetBtn.onclick=async()=>{
  try{await loadInstall(await GTAInstall.pick())}catch(e){fail(e)}
};

(async()=>{
  try{
    const saved=await GTAInstall.restore();
    if(saved) await loadInstall(saved);
  }catch(e){console.debug('restore failed',e)}
})();

function connect(name){
  const proto=location.protocol==='https:'?'wss':'ws';
  ws=new WebSocket(`${proto}://${location.host}/ws`);
  ui.net.textContent=' conectando';
  ws.onopen=()=>{ui.net.textContent=' online';ws.send(JSON.stringify({type:'hello',token,name}))};
  ws.onmessage=e=>{
    let m;try{m=JSON.parse(e.data)}catch{return}
    if(m.type==='welcome'){
      self={...m.self};token=m.token;localStorage.specterToken=token;mode=self.mode||'foot';
      ui.launcher.classList.add('hidden');ui.hud.classList.remove('hidden');ui.hint.classList.remove('hidden');ui.chat.classList.remove('hidden');
      msg(`Bem-vindo, ${self.name}`);msg('GTASA local assets: carregados.');
      canvas.requestPointerLock?.();
    }else if(m.type==='snapshot'){
      players.clear();for(const p of m.players)players.set(p.id,p);
      const me=players.get(self?.id);if(me&&self){Object.assign(self,me);mode=me.mode}
    }else if(m.type==='chat')msg(`${m.name}: ${m.text}`);
    else if(m.type==='system')msg(m.text);
  };
  ws.onclose=()=>{ui.net.textContent=' offline';if(!ui.launcher.classList.contains('hidden'))return;setTimeout(()=>connect(name),1800)};
}
ui.joinBtn.onclick=()=>{
  if(!worldReady)return fail(new Error('Carregue primeiro uma instalação válida do GTA San Andreas.'));
  connect(ui.name.value.trim()||'Player');
};

addEventListener('keydown',e=>{
  if(e.target===ui.chatInput)return;
  if(e.code==='Enter'&&!ui.launcher.classList.contains('hidden'))return;
  if(e.code==='Enter'){
    document.exitPointerLock?.();ui.chatInput.style.display='block';ui.chatInput.focus();return;
  }
  if(e.code==='KeyE'&&self){mode=mode==='car'?'foot':'car';ws?.send(JSON.stringify({type:'mode',mode}))}
  keys.add(e.code);
});
addEventListener('keyup',e=>keys.delete(e.code));
canvas.onclick=()=>{if(self)canvas.requestPointerLock?.()};
addEventListener('mousemove',e=>{
  if(document.pointerLockElement!==canvas)return;
  yaw-=e.movementX*.0024;
  pitch=Math.max(-0.12,Math.min(0.62,pitch-e.movementY*.0018));
});
ui.chatInput.onkeydown=e=>{
  if(e.key!=='Enter')return;
  const t=ui.chatInput.value.trim();if(t)ws?.send(JSON.stringify({type:'chat',text:t}));
  ui.chatInput.value='';ui.chatInput.style.display='none';canvas.requestPointerLock?.();
};
setInterval(()=>{
  if(!self||ws?.readyState!==1)return;
  ws.send(JSON.stringify({
    type:'input',
    f:(keys.has('KeyW')?1:0)-(keys.has('KeyS')?1:0),
    r:(keys.has('KeyD')?1:0)-(keys.has('KeyA')?1:0),
    sprint:keys.has('ShiftLeft')||keys.has('ShiftRight'),yaw
  }));
},50);

function worldPlayerPos(p){
  return [SPAWN[0]+(p?.x||0),SPAWN[1],SPAWN[2]+(p?.z||0)];
}
function drawMesh(mesh,matrix,color){
  gl.uniformMatrix4fv(U.model,false,matrix);gl.uniform3fv(U.color,color);
  gl.bindVertexArray(mesh.vao);gl.drawElements(gl.TRIANGLES,mesh.count,mesh.type,0);
}
let frames=0,lastFps=performance.now(),fps=0;
function render(now){
  const d=Math.min(devicePixelRatio||1,1.5),w=Math.max(1,Math.floor(innerWidth*d)),h=Math.max(1,Math.floor(innerHeight*d));
  if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;gl.viewport(0,0,w,h)}
  gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.disable(gl.CULL_FACE);
  gl.clearColor(.48,.68,.82,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);

  const me=worldPlayerPos(self);
  const target=[me[0],me[1]+1.7,me[2]];
  const dist=mode==='car'?15:11;
  const cp=Math.cos(pitch);
  const eye=[
    target[0]-Math.sin(yaw)*cp*dist,
    target[1]+Math.sin(pitch)*dist+3.0,
    target[2]-Math.cos(yaw)*cp*dist
  ];
  const vp=mul(perspective(Math.PI/3,w/h,.25,1300),lookAt(eye,target));
  gl.useProgram(prog);gl.uniformMatrix4fv(U.vp,false,vp);gl.uniform3fv(U.eye,eye);

  if(worldReady){
    const cull2=900*900;
    for(const inst of worldInstances){
      const dx=inst.position[0]-me[0],dz=inst.position[2]-me[2];
      if(dx*dx+dz*dz>cull2)continue;
      drawMesh(inst.mesh,inst.matrix,inst.color);
    }
  }

  for(const p of players.values()){
    const wp=worldPlayerPos(p),isMe=p.id===self?.id;
    const scale=p.mode==='car'?[1.8,.55,.9]:[.38,1,.38];
    const pos=[wp[0],wp[1]+scale[1],wp[2]];
    drawMesh(playerMesh,identityAt(pos,scale),isMe?[.95,.72,.12]:[.12,.42,.86]);
  }

  frames++;if(now-lastFps>500){fps=Math.round(frames*1000/(now-lastFps));frames=0;lastFps=now}
  if(self)ui.stats.textContent=`$${self.money??500} · ${mode.toUpperCase()} · ${players.size} player${players.size===1?'':'s'} · ${fps} FPS · ${worldInstances.length} GTASA inst`;
  requestAnimationFrame(render);
}
requestAnimationFrame(render);

console.info(LEGAL_NOTE);
