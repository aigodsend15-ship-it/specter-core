import * as THREE from 'three';
import * as CANNON from 'cannon-es';
import { GLTFLoader } from '/vendor/three/examples/jsm/loaders/GLTFLoader.js';

const $=s=>document.querySelector(s);
const ui={game:$('#game'),loading:$('#loading'),loadBar:$('#loadBar'),loadText:$('#loadText'),join:$('#join'),joinBtn:$('#joinBtn'),name:$('#name'),hud:$('#hud'),stats:$('#stats'),minimap:$('#minimap'),prompt:$('#prompt'),hint:$('#hint'),chat:$('#chat'),messages:$('#messages'),chatInput:$('#chatInput')};
const mm=ui.minimap.getContext('2d');
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const lerp=(a,b,t)=>a+(b-a)*t;
const ASSET_ROOT='https://raw.githubusercontent.com/Arslan12216775/kenney_car-kit/master/Models';
const BUILDINGS=['a','c','h','j','m','r'].map(x=>`${ASSET_ROOT}/City/building-type-${x}.glb`);
const CAR_URL=`${ASSET_ROOT}/GLB%20format/sedan-sports.glb`;
const loader=new GLTFLoader();
let loadProgress=0;
function loading(text,p){loadProgress=Math.max(loadProgress,p);ui.loadText.textContent=text;ui.loadBar.style.width=`${Math.round(loadProgress*100)}%`;}
function message(text,npc=false){const d=document.createElement('div');d.className='msg'+(npc?' npc':'');d.textContent=text;ui.messages.append(d);while(ui.messages.children.length>10)ui.messages.firstChild.remove();}

loading('Criando renderer e atmosfera…',.08);
const renderer=new THREE.WebGLRenderer({canvas:ui.game,antialias:true,powerPreference:'high-performance'});
renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.35));renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;
const scene=new THREE.Scene();scene.fog=new THREE.Fog(new THREE.Color(0x9cc7dc),120,480);
const camera=new THREE.PerspectiveCamera(64,innerWidth/innerHeight,.1,900);
const hemi=new THREE.HemisphereLight(0xd6ebff,0x4c5144,1.6);scene.add(hemi);
const sun=new THREE.DirectionalLight(0xfff0d5,2.1);sun.position.set(-80,130,55);scene.add(sun);
const clock=new THREE.Clock();
const daySky=new THREE.Color(0x78b6dd),nightSky=new THREE.Color(0x07111f),duskSky=new THREE.Color(0xd5876b);
scene.background=daySky.clone();

loading('Inicializando física rígida…',.16);
const physics=new CANNON.World({gravity:new CANNON.Vec3(0,-20,0)});physics.broadphase=new CANNON.SAPBroadphase(physics);physics.allowSleep=true;
const groundMat=new CANNON.Material('ground'),carMat=new CANNON.Material('car'),playerMat=new CANNON.Material('player');
physics.defaultContactMaterial.friction=.35;physics.defaultContactMaterial.restitution=.02;
physics.addContactMaterial(new CANNON.ContactMaterial(groundMat,carMat,{friction:.75,restitution:0,contactEquationStiffness:1e7}));
physics.addContactMaterial(new CANNON.ContactMaterial(groundMat,playerMat,{friction:0,restitution:0}));
const groundBody=new CANNON.Body({mass:0,material:groundMat});groundBody.addShape(new CANNON.Plane());groundBody.quaternion.setFromEuler(-Math.PI/2,0,0);physics.addBody(groundBody);

const ground=new THREE.Mesh(new THREE.PlaneGeometry(470,470),new THREE.MeshStandardMaterial({color:0x6f8067,roughness:1}));ground.rotation.x=-Math.PI/2;ground.position.y=-.015;scene.add(ground);
const roadMat3=new THREE.MeshStandardMaterial({color:0x25282d,roughness:.96});
const lineMat=new THREE.MeshBasicMaterial({color:0xd7c76e});
const cityRoot=new THREE.Group();scene.add(cityRoot);
const buildingSlots=[];
const staticMat=new CANNON.Material('static');
function addStaticBox(x,y,z,hx,hy,hz,yaw=0){const b=new CANNON.Body({mass:0,material:staticMat,position:new CANNON.Vec3(x,y,z)});b.addShape(new CANNON.Box(new CANNON.Vec3(hx,hy,hz)));b.quaternion.setFromEuler(0,yaw,0);physics.addBody(b);return b;}
function boxMesh(w,h,d,color){return new THREE.Mesh(new THREE.BoxGeometry(w,h,d),new THREE.MeshStandardMaterial({color,roughness:.88,metalness:.02}));}
function makeRoads(){
  for(let i=-4;i<=4;i++){
    const p=i*50;
    const r1=new THREE.Mesh(new THREE.PlaneGeometry(12,450),roadMat3);r1.rotation.x=-Math.PI/2;r1.position.set(p,.012,0);cityRoot.add(r1);
    const r2=new THREE.Mesh(new THREE.PlaneGeometry(450,12),roadMat3);r2.rotation.x=-Math.PI/2;r2.position.set(0,.014,p);cityRoot.add(r2);
    for(let q=-200;q<=200;q+=16){const a=new THREE.Mesh(new THREE.PlaneGeometry(.15,5),lineMat);a.rotation.x=-Math.PI/2;a.position.set(p,.02,q);cityRoot.add(a);const b=new THREE.Mesh(new THREE.PlaneGeometry(5,.15),lineMat);b.rotation.x=-Math.PI/2;b.position.set(q,.021,p);cityRoot.add(b);}
  }
}
function makeCity(){
  makeRoads();let idx=0;const colors=[0xc6a478,0x8799a5,0xb66f58,0xb8b18d,0x758776,0x9f8a76,0x7e7891];
  for(let gx=-4;gx<4;gx++)for(let gz=-4;gz<4;gz++){
    const cx=gx*50+25,cz=gz*50+25,positions=[[-11,-10],[11,10],[-10,11],[10,-11]];
    for(let s=0;s<2;s++){
      const [ox,oz]=positions[(idx+s)%positions.length],x=cx+ox,z=cz+oz,w=11+(idx*7%7),d=10+(idx*5%8),h=9+(idx*11%23);const fallback=boxMesh(w,h,d,colors[idx%colors.length]);fallback.position.set(x,h/2,z);cityRoot.add(fallback);addStaticBox(x,h/2,z,w/2,h/2,d/2);buildingSlots.push({x,z,w,d,h,fallback,asset:idx%BUILDINGS.length,yaw:((idx%4)*Math.PI)/2});idx++;
    }
  }
  const trunkMat=new THREE.MeshStandardMaterial({color:0x68503a}),leafMat=new THREE.MeshStandardMaterial({color:0x3d6846});
  for(let i=0;i<46;i++){const x=((i*73)%380)-190,z=((i*131)%380)-190;if(Math.abs((x+225)%50-25)<12||Math.abs((z+225)%50-25)<12)continue;const g=new THREE.Group(),t=new THREE.Mesh(new THREE.CylinderGeometry(.25,.35,2.4,6),trunkMat),l=new THREE.Mesh(new THREE.ConeGeometry(1.5,3.5,7),leafMat);t.position.y=1.2;l.position.y=3.3;g.add(t,l);g.position.set(x,0,z);cityRoot.add(g);}
  addStaticBox(0,4,-226,230,4,2);addStaticBox(0,4,226,230,4,2);addStaticBox(-226,4,0,2,4,230);addStaticBox(226,4,0,2,4,230);
}
makeCity();
async function loadArchitecture(){
  loading('Buscando arquitetura CC0…',.34);const templates=[];
  for(let i=0;i<BUILDINGS.length;i++){try{const gltf=await loader.loadAsync(BUILDINGS[i]);const root=gltf.scene;root.traverse(o=>{if(o.isMesh){o.castShadow=false;o.receiveShadow=true}});templates[i]=root;}catch{templates[i]=null;}loading(`Arquitetura CC0 ${i+1}/${BUILDINGS.length}`,.34+.18*((i+1)/BUILDINGS.length));}
  for(let i=0;i<buildingSlots.length;i++){const s=buildingSlots[i],src=templates[s.asset];if(!src)continue;const wrap=new THREE.Group(),clone=src.clone(true);clone.rotation.y=s.yaw;wrap.add(clone);let box=new THREE.Box3().setFromObject(clone),size=new THREE.Vector3();box.getSize(size);const scale=Math.min(1.35,Math.max(.35,Math.min(s.w/Math.max(size.x,.1),s.d/Math.max(size.z,.1))));clone.scale.setScalar(scale);box=new THREE.Box3().setFromObject(clone);const center=new THREE.Vector3();box.getCenter(center);clone.position.x-=center.x;clone.position.z-=center.z;clone.position.y-=box.min.y;wrap.position.set(s.x,.03,s.z);cityRoot.add(wrap);s.fallback.visible=false;}
}

function humanoid(color=0x52c8ff){
  const g=new THREE.Group(),mat=new THREE.MeshStandardMaterial({color,roughness:.7}),dark=new THREE.MeshStandardMaterial({color:0x17202a,roughness:.8});
  const torso=new THREE.Mesh(new THREE.CapsuleGeometry(.32,.72,4,7),mat);torso.position.y=1.25;const head=new THREE.Mesh(new THREE.SphereGeometry(.29,12,9),new THREE.MeshStandardMaterial({color:0xd8aa83}));head.position.y=2.08;const legL=new THREE.Mesh(new THREE.CapsuleGeometry(.12,.55,3,6),dark),legR=legL.clone();legL.position.set(-.18,.52,0);legR.position.set(.18,.52,0);g.add(torso,head,legL,legR);g.userData={legL,legR};return g;
}
const playerVisual=humanoid();scene.add(playerVisual);
const playerBody=new CANNON.Body({mass:78,material:playerMat,position:new CANNON.Vec3(0,1.15,8),linearDamping:.25,angularDamping:1,fixedRotation:true});playerBody.addShape(new CANNON.Box(new CANNON.Vec3(.42,1.02,.42)));playerBody.updateMassProperties();physics.addBody(playerBody);
let avatarYaw=0,jumpLatch=false;

loading('Montando veículo e suspensão…',.58);
const chassis=new CANNON.Body({mass:620,material:carMat,position:new CANNON.Vec3(5,1.25,3),angularDamping:.35,linearDamping:.06});chassis.addShape(new CANNON.Box(new CANNON.Vec3(1,.38,2.05)),new CANNON.Vec3(0,.15,0));physics.addBody(chassis);
const vehicle=new CANNON.RaycastVehicle({chassisBody:chassis,indexRightAxis:0,indexUpAxis:1,indexForwardAxis:2});
const wheelOptions={radius:.36,directionLocal:new CANNON.Vec3(0,-1,0),suspensionStiffness:32,suspensionRestLength:.36,frictionSlip:4.8,dampingRelaxation:2.3,dampingCompression:4.7,maxSuspensionForce:100000,rollInfluence:.03,axleLocal:new CANNON.Vec3(-1,0,0),maxSuspensionTravel:.28,customSlidingRotationalSpeed:-30,useCustomSlidingRotationalSpeed:true};
for(const [x,z,front] of [[-.92,-1.42,true],[.92,-1.42,true],[-.92,1.42,false],[.92,1.42,false]])vehicle.addWheel({...wheelOptions,chassisConnectionPointLocal:new CANNON.Vec3(x,0,z),isFrontWheel:front});vehicle.addToWorld(physics);
const carVisual=new THREE.Group();scene.add(carVisual);const carFallback=new THREE.Group();const lower=boxMesh(2,.55,4.15,0xd4343f);lower.position.y=.03;const cabin=boxMesh(1.65,.55,1.8,0x9d2029);cabin.position.set(0,.53,.25);carFallback.add(lower,cabin);carVisual.add(carFallback);
const wheelVisuals=[];for(let i=0;i<4;i++){const w=new THREE.Mesh(new THREE.CylinderGeometry(.36,.36,.27,16),new THREE.MeshStandardMaterial({color:0x111318,roughness:.9}));w.geometry.rotateZ(Math.PI/2);scene.add(w);wheelVisuals.push(w);}
async function loadCar(){try{const gltf=await loader.loadAsync(CAR_URL),root=gltf.scene;let box=new THREE.Box3().setFromObject(root),size=new THREE.Vector3();box.getSize(size);if(size.x>size.z){root.rotation.y=Math.PI/2;box=new THREE.Box3().setFromObject(root);box.getSize(size)}const scale=4.35/Math.max(size.x,size.z,.1);root.scale.setScalar(scale);box=new THREE.Box3().setFromObject(root);const center=box.getCenter(new THREE.Vector3());root.position.x-=center.x;root.position.z-=center.z;root.position.y+=-.48-box.min.y;root.traverse(o=>{if(o.isMesh)o.receiveShadow=true});carVisual.add(root);carFallback.visible=false;loading('Veículo CC0 carregado.',.78);}catch{loading('Veículo procedural pronto (fallback).',.78)}}

const npcVisuals=new Map(),buildVisuals=new Map(),remotePlayers=new Map();let serverNpcs=[],serverBuilds=[],worldTime=9;
const npcColors=[0x66d9ef,0xffc857,0xa8e063,0xff7c92,0xa98bff,0x65e0b8];
function ensureNpc(n){let e=npcVisuals.get(n.id);if(!e){const group=humanoid(npcColors[npcVisuals.size%npcColors.length]);group.scale.setScalar(.92);scene.add(group);e={group,target:new THREE.Vector3(n.x,0,n.z),yaw:n.yaw,phase:Math.random()*10};npcVisuals.set(n.id,e)}e.target.set(n.x,0,n.z);e.yaw=n.yaw;return e;}
function addBuildVisual(b){if(buildVisuals.has(b.id))return;let mesh;if(b.kind==='wall')mesh=boxMesh(4,2.2,.35,0x9a8066);else if(b.kind==='bench')mesh=boxMesh(2,.45,.65,0x77543d);else if(b.kind==='planter'){mesh=boxMesh(1.8,.55,1.8,0x805a3d);const plant=new THREE.Mesh(new THREE.ConeGeometry(.8,1.8,7),new THREE.MeshStandardMaterial({color:0x4d7d4c}));plant.position.y=1.15;mesh.add(plant)}else mesh=boxMesh(1.2,1.2,1.2,0xa57a4e);const hh=b.kind==='wall'?1.1:b.kind==='bench'?.225:b.kind==='planter'?.275:.6;mesh.position.set(b.x,(b.y||0)+hh,b.z);mesh.rotation.y=b.yaw||0;scene.add(mesh);buildVisuals.set(b.id,mesh);const sx=b.kind==='wall'?2:b.kind==='bench'?1:b.kind==='planter'?.9:.6,sy=hh,sz=b.kind==='wall'?.175:b.kind==='bench'?.325:b.kind==='planter'?.9:.6;addStaticBox(b.x,sy,b.z,sx,sy,sz,b.yaw||0);}
function applyWorld(w){if(!w)return;worldTime=Number(w.time??worldTime);serverNpcs=w.npcs||serverNpcs;serverBuilds=w.builds||serverBuilds;for(const n of serverNpcs)ensureNpc(n);for(const b of serverBuilds)addBuildVisual(b);}

let ws=null,self=null,token=localStorage.specterToken||null,mode='foot';const keys=new Set();let connectedName='Player';
function connect(name){connectedName=name;const proto=location.protocol==='https:'?'wss':'ws';ws=new WebSocket(`${proto}://${location.host}/ws`);message('Conectando ao mundo persistente…');ws.onopen=()=>ws.send(JSON.stringify({type:'hello',token,name}));ws.onmessage=e=>{let m;try{m=JSON.parse(e.data)}catch{return}if(m.type==='welcome'){self=m.self;token=m.token;localStorage.specterToken=token;mode=self.mode||'foot';playerBody.position.set(self.x,self.y||1.15,self.z);playerBody.velocity.setZero();applyWorld(m.world);ui.join.classList.add('hidden');ui.hud.classList.remove('hidden');ui.minimap.classList.remove('hidden');ui.hint.classList.remove('hidden');ui.chat.classList.remove('hidden');message(`Bem-vindo, ${self.name}. Mundo ${m.version}.`);initAudio();ui.game.requestPointerLock?.();}else if(m.type==='snapshot'){worldTime=m.time??worldTime;serverNpcs=m.npcs||serverNpcs;for(const n of serverNpcs)ensureNpc(n);updateRemotePlayers(m.players||[]);}else if(m.type==='build_added'){serverBuilds.push(m.build);addBuildVisual(m.build);message(`${m.build.owner} construiu ${m.build.kind}.`,true);}else if(m.type==='chat')message(`${m.name}: ${m.text}`);else if(m.type==='npc_chat')message(`${m.name}: ${m.text}`,true);else if(m.type==='system')message(m.text)};ws.onclose=()=>{message('Conexão perdida. Reconectando…');if(self)setTimeout(()=>connect(connectedName),1800)}}
function send(o){if(ws?.readyState===1)ws.send(JSON.stringify(o));}
function remoteCar(){const g=new THREE.Group(),a=boxMesh(1.8,.55,3.6,0x4f86d9),b=boxMesh(1.5,.5,1.5,0x315b9a);b.position.y=.48;g.add(a,b);return g;}
function updateRemotePlayers(list){const seen=new Set();for(const p of list){if(p.id===self?.id)continue;seen.add(p.id);let e=remotePlayers.get(p.id);if(!e){const group=new THREE.Group(),foot=humanoid(0xffbd66),car=remoteCar();group.add(foot,car);scene.add(group);e={group,foot,car,target:new THREE.Vector3(),yaw:0};remotePlayers.set(p.id,e)}e.target.set(p.x,p.y||0,p.z);e.yaw=p.yaw||0;e.foot.visible=p.mode!=='car';e.car.visible=p.mode==='car';}for(const [id,e] of remotePlayers)if(!seen.has(id)){scene.remove(e.group);remotePlayers.delete(id)}}

let camYaw=.25,camPitch=.24,lastMouse=0,lastE=0,lastF=0,lastB=0,lastR=0;
addEventListener('keydown',e=>{if(e.target===ui.chatInput)return;if(e.code==='Enter'&&self){document.exitPointerLock?.();ui.chatInput.style.display='block';ui.chatInput.focus();return}keys.add(e.code);if(e.repeat)return;if(e.code==='KeyE')toggleVehicle();if(e.code==='KeyF')talkNearest();if(e.code==='KeyB')buildNear();if(e.code==='KeyR')resetCar();});addEventListener('keyup',e=>keys.delete(e.code));
ui.game.addEventListener('click',()=>{if(self)ui.game.requestPointerLock?.()});addEventListener('mousemove',e=>{if(document.pointerLockElement!==ui.game)return;camYaw-=e.movementX*.0024;camPitch=clamp(camPitch-e.movementY*.0018,-.08,.68);lastMouse=performance.now()});
ui.chatInput.addEventListener('keydown',e=>{if(e.key!=='Enter')return;const t=ui.chatInput.value.trim();if(t)send({type:'chat',text:t});ui.chatInput.value='';ui.chatInput.style.display='none';ui.game.requestPointerLock?.()});
ui.joinBtn.onclick=()=>connect(ui.name.value.trim()||'Player');
function carDistance(){return Math.hypot(chassis.position.x-playerBody.position.x,chassis.position.z-playerBody.position.z)}
function toggleVehicle(){if(!self||performance.now()-lastE<250)return;lastE=performance.now();if(mode==='foot'&&carDistance()<4.8){mode='car';playerBody.sleep();playerBody.collisionResponse=false;playerVisual.visible=false;const f=new CANNON.Vec3(0,0,-1);chassis.quaternion.vmult(f,f);camYaw=Math.atan2(-f.x,-f.z);}else if(mode==='car'){mode='foot';playerBody.collisionResponse=true;const right=new CANNON.Vec3(1,0,0);chassis.quaternion.vmult(right,right);playerBody.position.set(chassis.position.x+right.x*2.2,Math.max(1.15,chassis.position.y),chassis.position.z+right.z*2.2);playerBody.velocity.setZero();playerBody.wakeUp();playerVisual.visible=true;}}
function nearestNpc(){let best=null,bd=Infinity;const x=mode==='car'?chassis.position.x:playerBody.position.x,z=mode==='car'?chassis.position.z:playerBody.position.z;for(const n of serverNpcs){const d=Math.hypot(n.x-x,n.z-z);if(d<bd){bd=d;best=n}}return bd<8?{n:best,d:bd}:null;}
function talkNearest(){if(performance.now()-lastF<250)return;lastF=performance.now();const hit=nearestNpc();if(hit)send({type:'talk_npc',id:hit.n.id,text:'Olá. O que você está fazendo e o que pretende construir aqui?'})}
function buildNear(){if(mode!=='foot'||performance.now()-lastB<300)return;lastB=performance.now();const fx=-Math.sin(camYaw),fz=-Math.cos(camYaw);send({type:'build',kind:['crate','wall','bench','planter'][Math.floor(performance.now()/1000)%4],x:playerBody.position.x+fx*3,z:playerBody.position.z+fz*3,yaw:camYaw})}
function resetCar(){if(performance.now()-lastR<300)return;lastR=performance.now();chassis.position.y=Math.max(2,chassis.position.y+1);chassis.quaternion.setFromEuler(0,camYaw,0);chassis.velocity.setZero();chassis.angularVelocity.setZero();chassis.wakeUp();}

let audioCtx=null,engineOsc=null,engineGain=null;function initAudio(){if(audioCtx)return;try{audioCtx=new AudioContext();engineOsc=audioCtx.createOscillator();engineGain=audioCtx.createGain();engineOsc.type='sawtooth';engineOsc.frequency.value=55;engineGain.gain.value=0;engineOsc.connect(engineGain).connect(audioCtx.destination);engineOsc.start();}catch{}}
function updateAudio(speed){if(!audioCtx)return;const target=mode==='car'?.018:0;engineGain.gain.setTargetAtTime(target,audioCtx.currentTime,.08);engineOsc.frequency.setTargetAtTime(48+Math.min(speed,120)*1.35,audioCtx.currentTime,.05);}

function applyFootControls(dt){
  const f=(keys.has('KeyW')?1:0)-(keys.has('KeyS')?1:0),r=(keys.has('KeyD')?1:0)-(keys.has('KeyA')?1:0),speed=keys.has('ShiftLeft')?8.2:5.2;let dx=0,dz=0;if(f||r){const forward=new THREE.Vector3(-Math.sin(camYaw),0,-Math.cos(camYaw)),right=new THREE.Vector3(Math.cos(camYaw),0,-Math.sin(camYaw));dx=forward.x*f+right.x*r;dz=forward.z*f+right.z*r;const l=Math.hypot(dx,dz)||1;dx/=l;dz/=l;avatarYaw=Math.atan2(dx,dz)+Math.PI;}
  const blend=Math.min(1,dt*12);playerBody.velocity.x=lerp(playerBody.velocity.x,dx*speed,blend);playerBody.velocity.z=lerp(playerBody.velocity.z,dz*speed,blend);const grounded=playerBody.position.y<1.2&&Math.abs(playerBody.velocity.y)<1.2;if(keys.has('Space')&&grounded&&!jumpLatch){playerBody.velocity.y=8.1;jumpLatch=true}if(!keys.has('Space'))jumpLatch=false;
}
function applyVehicleControls(){
  const throttle=(keys.has('KeyW')?1:0)-(keys.has('KeyS')?1:0),steer=(keys.has('KeyA')?1:0)-(keys.has('KeyD')?1:0),speed=chassis.velocity.length(),force=throttle>0?-2100:throttle<0?1250:0;vehicle.applyEngineForce(force,2);vehicle.applyEngineForce(force,3);const steerMax=.48*Math.max(.35,1-speed/42);vehicle.setSteeringValue(steer*steerMax,0);vehicle.setSteeringValue(steer*steerMax,1);const brake=keys.has('Space')?12:throttle===0?1.2:0;for(let i=0;i<4;i++)vehicle.setBrake(brake,i);
  if(speed>7){const localVel=new CANNON.Vec3();chassis.vectorToLocalFrame(chassis.velocity,localVel);const side=-localVel.x*Math.min(1.8,speed*.035),sideWorld=new CANNON.Vec3(side,0,0);chassis.vectorToWorldFrame(sideWorld,sideWorld);chassis.applyForce(sideWorld.scale(chassis.mass),chassis.position);chassis.applyForce(new CANNON.Vec3(0,-speed*speed*.7,0),chassis.position);}
}
function syncVehicleVisuals(){carVisual.position.copy(chassis.position);carVisual.quaternion.copy(chassis.quaternion);for(let i=0;i<vehicle.wheelInfos.length;i++){vehicle.updateWheelTransform(i);const t=vehicle.wheelInfos[i].worldTransform,w=wheelVisuals[i];w.position.copy(t.position);w.quaternion.copy(t.quaternion);}}
function syncPlayerVisual(t){if(mode==='foot'){playerVisual.position.set(playerBody.position.x,playerBody.position.y-1.04,playerBody.position.z);playerVisual.rotation.y=avatarYaw;const moving=Math.hypot(playerBody.velocity.x,playerBody.velocity.z)>.5,phase=t*.012;playerVisual.userData.legL.rotation.x=moving?Math.sin(phase)*.55:0;playerVisual.userData.legR.rotation.x=moving?-Math.sin(phase)*.55:0;}}
function syncNPCs(t,dt){for(const n of serverNpcs){const e=ensureNpc(n);e.group.position.lerp(e.target,Math.min(1,dt*4));e.group.rotation.y=lerp(e.group.rotation.y,e.yaw,Math.min(1,dt*4));e.group.userData.legL.rotation.x=Math.sin(t*.006+e.phase)*.35;e.group.userData.legR.rotation.x=-e.group.userData.legL.rotation.x;}for(const e of remotePlayers.values()){e.group.position.lerp(e.target,Math.min(1,dt*7));e.group.rotation.y=lerp(e.group.rotation.y,e.yaw,Math.min(1,dt*7));}}
function updateCamera(dt){
  const active=mode==='car'?chassis.position:playerBody.position,target=new THREE.Vector3(active.x,active.y+(mode==='car'?.7:1.05),active.z);if(mode==='car'&&performance.now()-lastMouse>1800&&chassis.velocity.length()>2){const f=new CANNON.Vec3(0,0,-1);chassis.quaternion.vmult(f,f);const behind=Math.atan2(-f.x,-f.z);let d=((behind-camYaw+Math.PI)%(Math.PI*2))-Math.PI;camYaw+=d*Math.min(1,dt*1.8);}const dist=mode==='car'?7.8:5.6,cp=Math.cos(camPitch),desired=new THREE.Vector3(target.x+Math.sin(camYaw)*dist*cp,target.y+1+Math.sin(camPitch)*dist,target.z+Math.cos(camYaw)*dist*cp);camera.position.lerp(desired,1-Math.exp(-dt*9));camera.lookAt(target);
}
function updateDayNight(dt){worldTime=(worldTime+dt*.0065)%24;const angle=(worldTime-6)/24*Math.PI*2,solar=Math.sin(angle),day=clamp((solar+.15)/.75,0,1),dusk=clamp(1-Math.abs(solar)/.28,0,1)*(1-day*.45),sky=nightSky.clone().lerp(daySky,day).lerp(duskSky,dusk*.45);scene.background.copy(sky);scene.fog.color.copy(sky);hemi.intensity=.25+day*1.45;sun.intensity=.05+day*2.1;sun.position.set(Math.cos(angle)*150,Math.max(8,solar*170),60);}
function activePosition(){return mode==='car'?chassis.position:playerBody.position}
function updatePrompt(){if(!self)return;const bits=[];if(mode==='foot'&&carDistance()<4.8)bits.push('<span class="key">E</span> entrar no carro');if(mode==='car')bits.push('<span class="key">E</span> sair do carro');const hit=nearestNpc();if(hit)bits.push(`<span class="key">F</span> falar com ${hit.n.name}`);ui.prompt.innerHTML=bits.join('&nbsp;&nbsp;·&nbsp;&nbsp;');ui.prompt.classList.toggle('hidden',bits.length===0);}
function drawMinimap(){if(ui.minimap.classList.contains('hidden'))return;const w=190,h=190,scale=.39,center=w/2,pos=activePosition();mm.clearRect(0,0,w,h);mm.save();mm.beginPath();mm.arc(center,center,94,0,Math.PI*2);mm.clip();mm.fillStyle='#07111b';mm.fillRect(0,0,w,h);mm.strokeStyle='#394550';mm.lineWidth=2;for(let r=-200;r<=200;r+=50){const x=center+(r-pos.x)*scale,z=center+(r-pos.z)*scale;mm.beginPath();mm.moveTo(x,0);mm.lineTo(x,h);mm.stroke();mm.beginPath();mm.moveTo(0,z);mm.lineTo(w,z);mm.stroke()}mm.fillStyle='#54d8ff';for(const n of serverNpcs){const x=center+(n.x-pos.x)*scale,y=center+(n.z-pos.z)*scale;if(x>0&&x<w&&y>0&&y<h){mm.beginPath();mm.arc(x,y,2.4,0,7);mm.fill()}}mm.fillStyle='#d69b5c';for(const b of serverBuilds.slice(-100)){const x=center+(b.x-pos.x)*scale,y=center+(b.z-pos.z)*scale;mm.fillRect(x-1,y-1,2,2)}mm.translate(center,center);mm.rotate(-camYaw);mm.fillStyle='#fff';mm.beginPath();mm.moveTo(0,-7);mm.lineTo(5,6);mm.lineTo(-5,6);mm.closePath();mm.fill();mm.restore();}

let netTimer=0,promptTimer=0,minimapTimer=0;
function frame(){requestAnimationFrame(frame);const dt=Math.min(.04,clock.getDelta()),t=performance.now();if(self){if(mode==='car')applyVehicleControls();else applyFootControls(dt)}physics.step(1/60,dt,3);syncVehicleVisuals();syncPlayerVisual(t);syncNPCs(t,dt);updateCamera(dt);updateDayNight(dt);const pos=activePosition();if(pos.y<-8){if(mode==='car')resetCar();else{playerBody.position.set(0,3,8);playerBody.velocity.setZero()}}if(self){netTimer+=dt;promptTimer+=dt;minimapTimer+=dt;if(netTimer>.1){netTimer=0;const p=activePosition(),vel=mode==='car'?chassis.velocity:playerBody.velocity;send({type:'state',x:p.x,y:p.y,z:p.z,yaw:mode==='car'?carHeading():avatarYaw,mode,vx:vel.x,vz:vel.z})}if(promptTimer>.15){promptTimer=0;updatePrompt()}if(minimapTimer>.08){minimapTimer=0;drawMinimap()}const kmh=Math.round((mode==='car'?chassis.velocity.length():Math.hypot(playerBody.velocity.x,playerBody.velocity.z))*3.6);ui.stats.textContent=`${mode.toUpperCase()} · ${kmh} km/h · ${remotePlayers.size+1} players · ${serverNpcs.length} minds · ${String(Math.floor(worldTime)).padStart(2,'0')}:${String(Math.floor((worldTime%1)*60)).padStart(2,'0')}`;updateAudio(kmh)}renderer.render(scene,camera);}
function carHeading(){const f=new CANNON.Vec3(0,0,-1);chassis.quaternion.vmult(f,f);return Math.atan2(f.x,f.z);}
function resize(){const w=innerWidth,h=innerHeight;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h,false)}addEventListener('resize',resize);resize();
async function boot(){try{const world=await fetch('/world').then(r=>r.json());applyWorld(world);loading('Mundo persistente sincronizado.',.25);}catch{loading('Mundo local inicializado.',.25)}await Promise.allSettled([loadArchitecture(),loadCar()]);loading('Física, cidade e WorldMind prontos.',1);setTimeout(()=>{ui.loading.classList.add('hidden');ui.join.classList.remove('hidden')},260);}
frame();boot();
