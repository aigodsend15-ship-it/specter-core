// SPECTER SA asset loader.
// Binary layout logic is independently ported to JavaScript from the MIT-licensed
// SanAndreasUnity project (notably ImageArchive.cs, ItemFile.cs and Instance.cs).
// See /THIRD_PARTY_NOTICES.md. No Rockstar assets are bundled or uploaded.

const SECTOR = 2048;
const td = new TextDecoder('windows-1252');
const ta = new TextDecoder('ascii');

function ascii(bytes) {
  let s = ta.decode(bytes);
  const z = s.indexOf('\0');
  return (z >= 0 ? s.slice(0, z) : s).trim();
}

function splitLine(line) {
  return line.split(',').flatMap(x => x.trim().split(/\s+/)).filter(Boolean);
}

function stripComment(line) {
  const p = line.indexOf('#');
  return (p >= 0 ? line.slice(0, p) : line).trim();
}

function cleanPath(p) {
  return p.replace(/\\/g, '/').replace(/^\.?\//, '').replace(/^["']|["']$/g, '');
}

export class ImgArchive {
  constructor(file, entries) {
    this.file = file;
    this.entries = entries;
    this.byName = new Map(entries.map(e => [e.name.toLowerCase(), e]));
  }

  static async open(file) {
    const h = new DataView(await file.slice(0, 8).arrayBuffer());
    const magic = String.fromCharCode(h.getUint8(0),h.getUint8(1),h.getUint8(2),h.getUint8(3));
    if (magic !== 'VER2') throw new Error(`models/gta3.img: versão ${magic || '?'} não suportada; esperado VER2`);
    const count = h.getUint32(4, true);
    if (!count || count > 200000) throw new Error(`gta3.img: contagem de entradas inválida (${count})`);
    const dir = new DataView(await file.slice(8, 8 + count * 32).arrayBuffer());
    const entries = [];
    for (let i=0;i<count;i++) {
      const o = i*32;
      const offset = dir.getUint32(o, true) * SECTOR;
      const sizeSecond = dir.getUint16(o+4, true);
      const sizeFirst = dir.getUint16(o+6, true);
      const size = (sizeFirst !== 0 ? sizeFirst : sizeSecond) * SECTOR;
      const name = ascii(new Uint8Array(dir.buffer, dir.byteOffset + o + 8, 24));
      if (!name || offset < 0 || offset + size > file.size + SECTOR) continue;
      entries.push({name, offset, size});
    }
    return new ImgArchive(file, entries);
  }

  get(name) { return this.byName.get(name.toLowerCase()); }
  has(name) { return this.byName.has(name.toLowerCase()); }
  byExt(ext) {
    ext = ext.toLowerCase();
    return this.entries.filter(e => e.name.toLowerCase().endsWith(ext));
  }
  async read(nameOrEntry) {
    const e = typeof nameOrEntry === 'string' ? this.get(nameOrEntry) : nameOrEntry;
    if (!e) throw new Error(`Arquivo não encontrado no IMG: ${nameOrEntry}`);
    return this.file.slice(e.offset, e.offset + e.size).arrayBuffer();
  }
  async prefix(nameOrEntry, n=64) {
    const e = typeof nameOrEntry === 'string' ? this.get(nameOrEntry) : nameOrEntry;
    if (!e) return null;
    return this.file.slice(e.offset, e.offset + Math.min(e.size,n)).arrayBuffer();
  }
}

class RootFS {
  constructor(root) {
    this.root = root;
    this.dirCache = new WeakMap();
  }
  async entries(dir) {
    let m = this.dirCache.get(dir);
    if (m) return m;
    m = new Map();
    for await (const [name, handle] of dir.entries()) m.set(name.toLowerCase(), handle);
    this.dirCache.set(dir,m);
    return m;
  }
  async handle(path) {
    const parts = cleanPath(path).split('/').filter(Boolean);
    let h = this.root;
    for (let i=0;i<parts.length;i++) {
      if (!h || h.kind !== 'directory') throw new Error(`Caminho inválido: ${path}`);
      const m = await this.entries(h);
      h = m.get(parts[i].toLowerCase());
      if (!h) throw new Error(`Não encontrado na instalação: ${path}`);
    }
    return h;
  }
  async maybe(path) {
    try { return await this.handle(path); } catch { return null; }
  }
  async file(path) {
    const h = await this.handle(path);
    if (h.kind !== 'file') throw new Error(`Esperado arquivo: ${path}`);
    return h.getFile();
  }
  async text(path) {
    return (await this.file(path)).text();
  }
}

function parseDat(text) {
  const refs = {ide:[], ipl:[], img:[]};
  for (const raw of text.split(/\r?\n/)) {
    const line = stripComment(raw);
    if (!line) continue;
    const m = line.match(/^(IDE|IPL|IMG)\s+(.+)$/i);
    if (!m) continue;
    const kind = m[1].toLowerCase();
    refs[kind].push(cleanPath(m[2].trim()));
  }
  return refs;
}

function parseIDE(text, defs) {
  let section = '';
  let n=0;
  for (const raw of text.split(/\r?\n/)) {
    let line = stripComment(raw);
    if (!line) continue;
    const lo=line.toLowerCase();
    if (lo === 'end') { section=''; continue; }
    if (!section && /^[a-z0-9_]+$/i.test(line)) { section=lo; continue; }
    if (!['objs','tobj','anim'].includes(section)) continue;
    const p=splitLine(line);
    const id=Number.parseInt(p[0],10);
    if (!Number.isFinite(id) || !p[1]) continue;
    defs.set(id,{id,model:p[1].toLowerCase(),txd:(p[2]||'').toLowerCase(),section});
    n++;
  }
  return n;
}

function parseTextIPL(text, source='loose') {
  const out=[];
  let section='';
  for (const raw of text.split(/\r?\n/)) {
    let line=stripComment(raw);
    if (!line) continue;
    const lo=line.toLowerCase();
    if (lo==='end') { section=''; continue; }
    if (!section && /^[a-z0-9_]+$/i.test(line)) { section=lo; continue; }
    if (section!=='inst') continue;
    const p=splitLine(line);
    if (p.length<11) continue;
    const objectId=Number.parseInt(p[0],10);
    const cellId=Number.parseInt(p[2],10);
    const v=p.slice(3,10).map(Number);
    const lodIndex=Number.parseInt(p[10],10);
    if (!Number.isFinite(objectId) || v.some(x=>!Number.isFinite(x))) continue;
    out.push({
      objectId,
      modelHint:(p[1]||'').toLowerCase(),
      cellId,
      position:[v[0],v[2],v[1]],
      rotation:[v[3],v[5],v[4],-v[6]],
      lodIndex:Number.isFinite(lodIndex)?lodIndex:-1,
      source
    });
  }
  return out;
}

function parseBinaryIPL(buffer, source='img') {
  const dv=new DataView(buffer);
  if (dv.byteLength<64) return [];
  const magic=String.fromCharCode(dv.getUint8(0),dv.getUint8(1),dv.getUint8(2),dv.getUint8(3));
  if (magic!=='bnry') return [];
  const count=dv.getInt32(4,true);
  const instOffset=dv.getInt32(28,true);
  if (count<0 || count>200000 || instOffset<0 || instOffset+count*40>dv.byteLength) return [];
  const out=[];
  let o=instOffset;
  for(let i=0;i<count;i++,o+=40){
    const posX=dv.getFloat32(o,true), posZ=dv.getFloat32(o+4,true), posY=dv.getFloat32(o+8,true);
    const qx=dv.getFloat32(o+12,true), qz=dv.getFloat32(o+16,true), qy=dv.getFloat32(o+20,true), qw=dv.getFloat32(o+24,true);
    const objectId=dv.getInt32(o+28,true), cellId=dv.getInt32(o+32,true), lodIndex=dv.getInt32(o+36,true);
    if (![posX,posY,posZ,qx,qy,qz,qw].every(Number.isFinite)) continue;
    out.push({
      objectId, modelHint:'', cellId,
      position:[posX,posY,posZ],
      rotation:[qx,qy,qz,-qw],
      lodIndex, source
    });
  }
  return out;
}

function rwHeader(dv,o,end=dv.byteLength){
  if(o<0 || o+12>end) return null;
  const type=dv.getUint32(o,true), size=dv.getUint32(o+4,true), version=dv.getUint32(o+8,true);
  const data=o+12, chunkEnd=data+size;
  if(size>0x40000000 || chunkEnd>end || chunkEnd<data) return null;
  return {type,size,version,start:o,data,end:chunkEnd};
}
function rwChildren(dv,start,end){
  const a=[]; let o=start, guard=0;
  while(o+12<=end && guard++<100000){
    const h=rwHeader(dv,o,end);
    if(!h) break;
    a.push(h);
    if(h.end<=o) break;
    o=h.end;
  }
  return a;
}

function readV3(dv,o){ return [dv.getFloat32(o,true),dv.getFloat32(o+4,true),dv.getFloat32(o+8,true)]; }
function transformPointOriginal(v, frames, idx){
  let p=v, guard=0;
  while(idx>=0 && idx<frames.length && guard++<128){
    const f=frames[idx];
    const [x,y,z]=p, r=f.right, fw=f.forward, up=f.up, t=f.pos;
    p=[
      r[0]*x + fw[0]*y + up[0]*z + t[0],
      r[1]*x + fw[1]*y + up[1]*z + t[1],
      r[2]*x + fw[2]*y + up[2]*z + t[2]
    ];
    idx=f.parent;
  }
  return p;
}
function axisSwap(v){ return [v[0],v[2],v[1]]; }

function parseFrameList(dv,h){
  const ch=rwChildren(dv,h.data,h.end);
  const st=ch.find(x=>x.type===1);
  if(!st || st.size<4) return [];
  const count=dv.getUint32(st.data,true);
  if(count>10000 || 4+count*56>st.size) return [];
  const frames=[]; let o=st.data+4;
  for(let i=0;i<count;i++,o+=56){
    frames.push({
      right:readV3(dv,o),
      forward:readV3(dv,o+12),
      up:readV3(dv,o+24),
      pos:readV3(dv,o+36),
      parent:dv.getInt32(o+48,true),
      flags:dv.getUint32(o+52,true)
    });
  }
  return frames;
}

function parseGeometry(dv,h){
  const ch=rwChildren(dv,h.data,h.end);
  const st=ch.find(x=>x.type===1);
  if(!st || st.size<16) return null;
  let o=st.data;
  const flags=dv.getUint16(o,true); o+=2;
  const uvCount=dv.getUint8(o); o++;
  o++;
  const faceCount=dv.getUint32(o,true); o+=4;
  const vertexCount=dv.getUint32(o,true); o+=4;
  const morphCount=dv.getUint32(o,true); o+=4;
  if(vertexCount>1000000 || faceCount>2000000 || morphCount>64) return null;

  function decode(start){
    let p=start;
    if(flags & 8) p += vertexCount*4;
    if(flags & (4|128)) p += uvCount*vertexCount*8;
    const faceStart=p; p+=faceCount*8;
    if(p+24>st.end) return null;
    const faces=new Uint32Array(faceCount*3);
    let fo=faceStart;
    for(let i=0;i<faceCount;i++,fo+=8){
      const v1=dv.getUint16(fo,true), v0=dv.getUint16(fo+2,true), v2=dv.getUint16(fo+6,true);
      if(v0>=vertexCount||v1>=vertexCount||v2>=vertexCount) return null;
      faces[i*3]=v0; faces[i*3+1]=v2; faces[i*3+2]=v1;
    }
    p+=16;
    const hasPos=dv.getUint32(p,true); p+=4;
    const hasNormals=dv.getUint32(p,true); p+=4;
    if(hasPos>1 || hasNormals>1) return null;
    if(!(flags & 2) || hasPos===0) return null;
    if(p+vertexCount*12>st.end) return null;
    const verts=new Float32Array(vertexCount*3);
    for(let i=0;i<vertexCount;i++,p+=12){
      verts[i*3]=dv.getFloat32(p,true);
      verts[i*3+1]=dv.getFloat32(p+4,true);
      verts[i*3+2]=dv.getFloat32(p+8,true);
    }
    return {verts,indices:faces};
  }
  return decode(o) || decode(o+12);
}

export function parseDFF(buffer){
  const dv=new DataView(buffer);
  const root=rwHeader(dv,0);
  if(!root || root.type!==16) throw new Error('DFF sem clump RenderWare válido');
  const cc=rwChildren(dv,root.data,root.end);
  const frameChunk=cc.find(x=>x.type===14);
  const geomList=cc.find(x=>x.type===26);
  if(!geomList) throw new Error('DFF sem GeometryList');
  const frames=frameChunk ? parseFrameList(dv,frameChunk) : [];
  const glc=rwChildren(dv,geomList.data,geomList.end);
  const geoms=glc.filter(x=>x.type===15).map(x=>parseGeometry(dv,x));
  const atomics=[];
  for(const a of cc.filter(x=>x.type===20)){
    const ac=rwChildren(dv,a.data,a.end);
    const st=ac.find(x=>x.type===1);
    if(!st || st.size<16) continue;
    atomics.push({
      frame:dv.getUint32(st.data,true),
      geometry:dv.getUint32(st.data+4,true),
      flags:dv.getUint32(st.data+8,true)
    });
  }
  if(!atomics.length) {
    for(let i=0;i<geoms.length;i++) atomics.push({frame:-1,geometry:i,flags:4});
  }

  const outV=[], outI=[]; let base=0;
  for(const a of atomics){
    if(!(a.flags&4)) continue;
    const g=geoms[a.geometry];
    if(!g) continue;
    for(let i=0;i<g.verts.length;i+=3){
      const p=[g.verts[i],g.verts[i+1],g.verts[i+2]];
      const q=axisSwap(transformPointOriginal(p,frames,a.frame));
      outV.push(q[0],q[1],q[2]);
    }
    for(const idx of g.indices) outI.push(base+idx);
    base += g.verts.length/3;
  }
  if(!outV.length || !outI.length) throw new Error('DFF sem geometria renderizável');
  const maxIndex=outV.length/3-1;
  const indices=maxIndex<65536 ? new Uint16Array(outI) : new Uint32Array(outI);
  return {vertices:new Float32Array(outV),indices};
}

async function openSavedHandle(){
  return new Promise((resolve)=>{
    const req=indexedDB.open('specter-sa',1);
    req.onupgradeneeded=()=>req.result.createObjectStore('handles');
    req.onerror=()=>resolve(null);
    req.onsuccess=()=>{
      const db=req.result, tx=db.transaction('handles','readonly');
      const g=tx.objectStore('handles').get('gtasa');
      g.onsuccess=()=>resolve(g.result||null);
      g.onerror=()=>resolve(null);
    };
  });
}
async function saveHandle(h){
  return new Promise((resolve)=>{
    const req=indexedDB.open('specter-sa',1);
    req.onupgradeneeded=()=>req.result.createObjectStore('handles');
    req.onerror=()=>resolve();
    req.onsuccess=()=>{
      const tx=req.result.transaction('handles','readwrite');
      tx.objectStore('handles').put(h,'gtasa');
      tx.oncomplete=()=>resolve();
      tx.onerror=()=>resolve();
    };
  });
}

export class GTAInstall {
  constructor(root){ this.fs=new RootFS(root); this.root=root; this.defs=new Map(); this.placements=[]; this.modelCache=new Map(); }

  static async pick() {
    if (!window.showDirectoryPicker) throw new Error('Seu navegador não oferece seleção segura de pasta. Use Chrome/Edge desktop.');
    const root=await window.showDirectoryPicker({mode:'read'});
    await saveHandle(root);
    return new GTAInstall(root);
  }
  static async restore() {
    const root=await openSavedHandle();
    if(!root) return null;
    try{
      if((await root.queryPermission({mode:'read'}))!=='granted') return null;
      return new GTAInstall(root);
    }catch{return null;}
  }

  async initialize(progress=()=>{}) {
    progress('Validando instalação GTA San Andreas…',0.02);
    const gtaDat=await this.fs.text('data/gta.dat');
    const imgFile=await this.fs.file('models/gta3.img');
    if(imgFile.size<100_000_000) throw new Error('models/gta3.img parece incompleto. Selecione a pasta do GTA San Andreas clássico para PC.');
    progress('Indexando models/gta3.img…',0.08);
    this.img=await ImgArchive.open(imgFile);
    const refs=parseDat(gtaDat);

    progress(`Lendo definições IDE (${refs.ide.length})…`,0.12);
    let done=0;
    for(const path of refs.ide){
      const h=await this.fs.maybe(path);
      if(!h || h.kind!=='file') { done++; continue; }
      try { parseIDE(await (await h.getFile()).text(),this.defs); } catch {}
      done++;
      progress(`IDE ${done}/${refs.ide.length} · ${this.defs.size} objetos`,0.12+0.18*(done/Math.max(1,refs.ide.length)));
    }

    progress('Lendo placements IPL da instalação…',0.31);
    done=0;
    for(const path of refs.ipl){
      const h=await this.fs.maybe(path);
      if(h?.kind==='file'){
        try{
          const f=await h.getFile();
          const pre=await f.slice(0,4).arrayBuffer();
          const magic=ascii(new Uint8Array(pre));
          if(magic==='bnry') this.placements.push(...parseBinaryIPL(await f.arrayBuffer(),path));
          else this.placements.push(...parseTextIPL(await f.text(),path));
        }catch{}
      }
      done++;
      if(done%4===0) progress(`IPL local ${done}/${refs.ipl.length}`,0.31+0.09*(done/Math.max(1,refs.ipl.length)));
    }

    const streams=this.img.byExt('.ipl');
    progress(`Lendo ${streams.length} IPLs de streaming do gta3.img…`,0.41);
    let streamDone=0;
    for(const e of streams){
      try{
        const pre=await this.img.prefix(e,4);
        if(ascii(new Uint8Array(pre))==='bnry') this.placements.push(...parseBinaryIPL(await this.img.read(e),e.name));
      }catch{}
      streamDone++;
      if(streamDone%16===0) progress(`IPL stream ${streamDone}/${streams.length} · ${this.placements.length} instâncias`,0.41+0.20*(streamDone/Math.max(1,streams.length)));
    }
    progress(`Instalação pronta: ${this.defs.size} definições, ${this.placements.length} instâncias, ${this.img.entries.length} arquivos IMG`,0.62);
    return this;
  }

  nearby(center=[2495,15,-1685], radius=650, max=1400){
    const r2=radius*radius, out=[];
    for(const p of this.placements){
      if((p.cellId&255)!==0) continue;
      const dx=p.position[0]-center[0], dz=p.position[2]-center[2];
      if(dx*dx+dz*dz>r2) continue;
      const d=this.defs.get(p.objectId);
      const model=(d?.model || p.modelHint || '').toLowerCase();
      if(!model || !this.img.has(model+'.dff')) continue;
      out.push({...p,model,dist2:dx*dx+dz*dz});
    }
    out.sort((a,b)=>a.dist2-b.dist2);
    const nonLod=out.filter(x=>x.lodIndex<0);
    return (nonLod.length>200 ? nonLod : out).slice(0,max);
  }

  async model(name){
    name=name.toLowerCase();
    if(this.modelCache.has(name)) return this.modelCache.get(name);
    const promise=(async()=>parseDFF(await this.img.read(name+'.dff')))();
    this.modelCache.set(name,promise);
    try{return await promise}catch(e){this.modelCache.delete(name);throw e}
  }
}

export const LEGAL_NOTE =
  'Os arquivos GTA:SA são lidos somente da pasta escolhida por você e permanecem no navegador. O servidor não hospeda nem recebe assets da Rockstar.';
