import * as THREE from 'https://esm.sh/three@0.180.0';
import { GLTFLoader } from 'https://esm.sh/three@0.180.0/examples/jsm/loaders/GLTFLoader.js';

const $ = (s) => document.querySelector(s);
const canvas = $('#game');
const ui = {
  launcher: $('#launcher'), joinBtn: $('#joinBtn'), name: $('#name'),
  loadState: $('#loadState'), progressBar: $('#progressBar'), assetStats: $('#assetStats'),
  hud: $('#hud'), net: $('#net'), stats: $('#stats'), hint: $('#hint'),
  chat: $('#chat'), messages: $('#messages'), chatInput: $('#chatInput')
};

const renderer = new THREE.WebGLRenderer({canvas, antialias:true, powerPreference:'high-performance'});
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
renderer.setSize(innerWidth, innerHeight, false);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x82b8df);
scene.fog = new THREE.Fog(0x82b8df, 150, 650);

const camera = new THREE.PerspectiveCamera(62, innerWidth/innerHeight, 0.1, 1200);
const hemi = new THREE.HemisphereLight(0xdbeeff, 0x3f4b2e, 1.55);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xfff0d3, 2.0);
sun.position.set(-120, 180, 90);
scene.add(sun);

const ROAD_SPACING = 56;
const ROAD_WIDTH = 12;
const BLOCK = ROAD_SPACING - ROAD_WIDTH;

const groundMat = new THREE.MeshStandardMaterial({color:0x5d8450, roughness:1});
const ground = new THREE.Mesh(new THREE.PlaneGeometry(760,760), groundMat);
ground.rotation.x = -Math.PI/2;
ground.position.y = -0.03;
scene.add(ground);

const roadMat = new THREE.MeshStandardMaterial({color:0x30343a, roughness:0.95});
const curbMat = new THREE.MeshStandardMaterial({color:0x94999b, roughness:1});
const laneMat = new THREE.MeshBasicMaterial({color:0xd9c36a});

function plane(w,d,mat,x,z,y=0){
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w,d),mat);
  m.rotation.x=-Math.PI/2;m.position.set(x,y,z);scene.add(m);return m;
}
for(let i=-5;i<=5;i++){
  const p=i*ROAD_SPACING;
  plane(ROAD_WIDTH,650,roadMat,p,0,.005);
  plane(650,ROAD_WIDTH,roadMat,0,p,.005);
  plane(.12,650,laneMat,p,0,.012);
  plane(650,.12,laneMat,0,p,.012);
  plane(1.2,650,curbMat,p-ROAD_WIDTH/2-.7,0,.009);
  plane(1.2,650,curbMat,p+ROAD_WIDTH/2+.7,0,.009);
  plane(650,1.2,curbMat,0,p-ROAD_WIDTH/2-.7,.009);
  plane(650,1.2,curbMat,0,p+ROAD_WIDTH/2+.7,.009);
}

const seedRand=(()=>{let s=0x51a5f00d;return()=>((s=Math.imul(s,1664525)+1013904223>>>0)/4294967296)})();
const buildingURLs='abcdefghij'.split('').map(ch=>`https://raw.githubusercontent.com/petroulacl/fps-buildings-env-kit/main/buildings/kenney-city-kit-suburban/Models/GLB%20format/building-type-${ch}.glb`);
const loader=new GLTFLoader();
const prototypes=[];

function setProgress(text,p){ui.loadState.textContent=text;ui.progressBar.style.width=`${Math.round(Math.max(0,Math.min(1,p))*100)}%`}
function fallbackBuilding(color=0xb9aa91){
  const g=new THREE.Group();
  const h=8+seedRand()*16,w=8+seedRand()*8,d=8+seedRand()*8;
  const body=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),new THREE.MeshStandardMaterial({color,roughness:.9}));body.position.y=h/2;g.add(body);
  const roof=new THREE.Mesh(new THREE.BoxGeometry(w*.82,.6,d*.82),new THREE.MeshStandardMaterial({color:0x5c5e61,roughness:1}));roof.position.y=h+.3;g.add(roof);
  return g;
}
async function loadPrototype(url){
  try{
    const gltf=await loader.loadAsync(url);
    const root=gltf.scene;
    const box=new THREE.Box3().setFromObject(root);
    const size=box.getSize(new THREE.Vector3());
    root.scale.multiplyScalar(15/Math.max(size.x,size.z,.001));
    const fixed=new THREE.Group();fixed.add(root);
    const b2=new THREE.Box3().setFromObject(fixed);fixed.position.y-=b2.min.y;
    fixed.traverse(o=>{if(o.isMesh){o.castShadow=false;o.receiveShadow=false}});
    return fixed;
  }catch(e){console.warn('CC0 building fallback',url,e);return fallbackBuilding()}
}
function addTree(x,z,s=1){
  const trunk=new THREE.Mesh(new THREE.CylinderGeometry(.18*s,.28*s,2.1*s,7),new THREE.MeshStandardMaterial({color:0x6f4d2f}));trunk.position.set(x,1.05*s,z);scene.add(trunk);
  const crown=new THREE.Mesh(new THREE.IcosahedronGeometry(1.05*s,1),new THREE.MeshStandardMaterial({color:0x3f7d45,roughness:1}));crown.position.set(x,2.7*s,z);scene.add(crown);
}
function addLamp(x,z,rot=0){
  const g=new THREE.Group();const poleMat=new THREE.MeshStandardMaterial({color:0x3d4248,metalness:.45,roughness:.55});
  const pole=new THREE.Mesh(new THREE.CylinderGeometry(.06,.08,4.8,8),poleMat);pole.position.y=2.4;g.add(pole);
  const arm=new THREE.Mesh(new THREE.BoxGeometry(.08,.08,1.05),poleMat);arm.position.set(.45,4.75,0);g.add(arm);
  const bulb=new THREE.Mesh(new THREE.BoxGeometry(.35,.12,.22),new THREE.MeshStandardMaterial({color:0xf5e2a7,emissive:0x493d16,emissiveIntensity:.3}));bulb.position.set(.95,4.68,0);g.add(bulb);
  g.position.set(x,0,z);g.rotation.y=rot;scene.add(g);
}
async function buildCity(){
  setProgress('Carregando prédios CC0 da web…',.08);
  let done=0;
  for(const url of buildingURLs){prototypes.push(await loadPrototype(url));done++;setProgress(`Carregando assets CC0: ${done}/${buildingURLs.length}`,.08+.42*done/buildingURLs.length)}
  let count=0;
  for(let gx=-5;gx<5;gx++)for(let gz=-5;gz<5;gz++){
    const cx=gx*ROAD_SPACING+ROAD_SPACING/2,cz=gz*ROAD_SPACING+ROAD_SPACING/2;
    if((gx+gz*3)%11===0){plane(BLOCK-4,BLOCK-4,new THREE.MeshStandardMaterial({color:0x4f8d52,roughness:1}),cx,cz,.01);for(let t=0;t<7;t++)addTree(cx+(seedRand()-.5)*(BLOCK-9),cz+(seedRand()-.5)*(BLOCK-9),.85+seedRand()*.5);continue}
    for(const [ox,oz] of [[-11,-11],[11,-11],[-11,11],[11,11]]){
      if(seedRand()<.13)continue;
      const p=prototypes[Math.floor(seedRand()*prototypes.length)].clone(true);
      const box=new THREE.Box3().setFromObject(p),sz=box.getSize(new THREE.Vector3());
      p.scale.multiplyScalar((10+seedRand()*6)/Math.max(sz.x,sz.z,1));
      p.position.set(cx+ox+(seedRand()-.5)*2.6,0,cz+oz+(seedRand()-.5)*2.6);p.rotation.y=Math.floor(seedRand()*4)*Math.PI/2;scene.add(p);count++;
    }
  }
  for(let i=-5;i<=5;i++)for(let j=-5;j<=5;j+=2)addLamp(i*ROAD_SPACING+ROAD_WIDTH/2+2,j*ROAD_SPACING+16,Math.PI);
  ui.assetStats.textContent=`${count} prédios · ${buildingURLs.length} modelos CC0 · mundo 100% original`;
  setProgress('Cidade pronta. Clique em JOGAR AGORA.',1);ui.joinBtn.disabled=false;
}

function makeAvatar(color=0x2796e8){
  const g=new THREE.Group(),mat=new THREE.MeshStandardMaterial({color,roughness:.85}),skin=new THREE.MeshStandardMaterial({color:0xd39a73,roughness:.9});
  const body=new THREE.Mesh(new THREE.BoxGeometry(.62,.95,.34),mat);body.position.y=1.35;g.add(body);
  const head=new THREE.Mesh(new THREE.SphereGeometry(.28,12,10),skin);head.position.y=2.02;g.add(head);
  for(const x of[-.18,.18]){const leg=new THREE.Mesh(new THREE.BoxGeometry(.18,.72,.2),new THREE.MeshStandardMaterial({color:0x26364b}));leg.position.set(x,.48,0);g.add(leg)}
  return g;
}
function makeCar(color=0xe06a36){
  const g=new THREE.Group(),bodyMat=new THREE.MeshStandardMaterial({color,metalness:.15,roughness:.55}),glass=new THREE.MeshStandardMaterial({color:0x203a4c,metalness:.15,roughness:.3}),tire=new THREE.MeshStandardMaterial({color:0x121416,roughness:1});
  const body=new THREE.Mesh(new THREE.BoxGeometry(3.7,.65,1.75),bodyMat);body.position.y=.62;g.add(body);
  const cabin=new THREE.Mesh(new THREE.BoxGeometry(1.9,.65,1.52),glass);cabin.position.set(-.15,1.15,0);g.add(cabin);
  for(const x of[-1.18,1.18])for(const z of[-.82,.82]){const w=new THREE.Mesh(new THREE.CylinderGeometry(.32,.32,.22,12),tire);w.rotation.x=Math.PI/2;w.position.set(x,.38,z);g.add(w)}
  return g;
}
const localAvatar=makeAvatar(0x2f95de);scene.add(localAvatar);
const localCar=makeCar(0xe05d2f);localCar.visible=false;scene.add(localCar);
const remotes=new Map();
let ws=null,self=null,token=localStorage.specterToken||null,yaw=Math.PI,pitch=.24,mode='foot',carSpeed=0;
const players=new Map(),keys=new Set();
function msg(t){const d=document.createElement('div');d.className='msg';d.textContent=t;ui.messages.append(d);while(ui.messages.children.length>9)ui.messages.firstChild.remove()}
function connect(name){
  const proto=location.protocol==='https:'?'wss':'ws';ws=new WebSocket(`${proto}://${location.host}/ws`);ui.net.textContent=' conectando';
  ws.onopen=()=>{ui.net.textContent=' online';ws.send(JSON.stringify({type:'hello',token,name}))};
  ws.onmessage=e=>{let m;try{m=JSON.parse(e.data)}catch{return}
    if(m.type==='welcome'){self={...m.self};token=m.token;localStorage.specterToken=token;mode=self.mode||'foot';ui.launcher.classList.add('hidden');ui.hud.classList.remove('hidden');ui.hint.classList.remove('hidden');ui.chat.classList.remove('hidden');msg(`Bem-vindo, ${self.name}`);canvas.requestPointerLock?.()}
    else if(m.type==='snapshot'){players.clear();for(const p of m.players)players.set(p.id,p);const me=players.get(self?.id);if(me&&self){self.x=me.x;self.z=me.z;self.money=me.money;mode=me.mode||mode}}
    else if(m.type==='chat')msg(`${m.name}: ${m.text}`);else if(m.type==='system')msg(m.text)
  };
  ws.onclose=()=>{ui.net.textContent=' offline';if(self)setTimeout(()=>connect(name),1800)};
}
ui.joinBtn.onclick=()=>connect(ui.name.value.trim()||'Player');
addEventListener('keydown',e=>{if(e.target===ui.chatInput)return;if(e.code==='Enter'&&self){document.exitPointerLock?.();ui.chatInput.style.display='block';ui.chatInput.focus();return}if(e.code==='KeyE'&&self){mode=mode==='car'?'foot':'car';ws?.send(JSON.stringify({type:'mode',mode}))}keys.add(e.code)});
addEventListener('keyup',e=>keys.delete(e.code));
canvas.addEventListener('click',()=>self&&canvas.requestPointerLock?.());
addEventListener('mousemove',e=>{if(document.pointerLockElement!==canvas)return;if(mode==='foot')yaw-=e.movementX*.00235;pitch=THREE.MathUtils.clamp(pitch-e.movementY*.0016,-.08,.58)});
ui.chatInput.onkeydown=e=>{if(e.key==='Enter'){const t=ui.chatInput.value.trim();if(t)ws?.send(JSON.stringify({type:'chat',text:t}));ui.chatInput.value='';ui.chatInput.style.display='none';canvas.requestPointerLock?.()}};
setInterval(()=>{
  if(!self||ws?.readyState!==1)return;
  let f=(keys.has('KeyW')?1:0)-(keys.has('KeyS')?1:0),r=(keys.has('KeyD')?1:0)-(keys.has('KeyA')?1:0);
  if(mode==='car'){carSpeed+=f*.065;carSpeed*=.965;carSpeed=THREE.MathUtils.clamp(carSpeed,-.52,1);yaw-=r*.038*Math.max(.18,Math.abs(carSpeed))*Math.sign(carSpeed||1);f=carSpeed;r=0}
  ws.send(JSON.stringify({type:'input',f,r,sprint:keys.has('ShiftLeft'),yaw}));
},50);
function syncRemote(){
  const live=new Set();
  for(const p of players.values()){
    if(p.id===self?.id)continue;live.add(p.id);let item=remotes.get(p.id);
    if(!item){item={foot:makeAvatar(0x347ad3),car:makeCar(0x3a78d6)};scene.add(item.foot,item.car);remotes.set(p.id,item)}
    const isCar=p.mode==='car';item.foot.visible=!isCar;item.car.visible=isCar;const obj=isCar?item.car:item.foot;obj.position.set(p.x,0,p.z);obj.rotation.y=p.yaw||0;
  }
  for(const [id,item] of remotes)if(!live.has(id)){scene.remove(item.foot,item.car);remotes.delete(id)}
}
const clock=new THREE.Clock();
function animate(){
  requestAnimationFrame(animate);const dt=Math.min(clock.getDelta(),.05),x=self?.x||0,z=self?.z||0;
  localAvatar.visible=!!self&&mode==='foot';localCar.visible=!!self&&mode==='car';const local=mode==='car'?localCar:localAvatar;local.position.set(x,0,z);local.rotation.y=yaw;syncRemote();
  const dist=mode==='car'?10:7.2,h=mode==='car'?4.8:3.9,back=new THREE.Vector3(Math.sin(yaw)*dist,0,Math.cos(yaw)*dist),desired=new THREE.Vector3(x-back.x,h+pitch*7,z-back.z);
  camera.position.lerp(desired,1-Math.pow(.001,dt));camera.lookAt(x,1.25+pitch*1.7,z);
  ui.stats.textContent=`$${self?.money??500} · ${mode.toUpperCase()} · ${players.size} online · ${Math.round(renderer.info.render.triangles/1000)}k tris`;
  renderer.render(scene,camera);
}
animate();
addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));renderer.setSize(innerWidth,innerHeight,false)});
buildCity().catch(e=>{console.error(e);setProgress('Falha ao carregar assets externos; usando cidade procedural.',1);ui.joinBtn.disabled=false;ui.assetStats.textContent='Modo fallback procedural ativo.'});