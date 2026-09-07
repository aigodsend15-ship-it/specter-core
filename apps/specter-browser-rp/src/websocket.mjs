import crypto from 'node:crypto';
import { EventEmitter } from 'node:events';

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';
export const WS_OPEN = 1;

export class TinyWebSocket extends EventEmitter {
  constructor(socket, head = Buffer.alloc(0), maxPayload = 16 * 1024) {
    super(); this.socket = socket; this.maxPayload = maxPayload; this.readyState = WS_OPEN;
    this.buffer = head.length ? Buffer.from(head) : Buffer.alloc(0);
    socket.on('data', chunk => this.#onData(chunk));
    socket.on('close', () => { if (this.readyState !== 3) { this.readyState = 3; this.emit('close'); } });
    socket.on('error', err => this.emit('error', err));
    if (this.buffer.length) queueMicrotask(() => this.#parse());
  }
  send(data) { if (this.readyState !== WS_OPEN) return false; return this.socket.write(frame(0x1, Buffer.from(String(data)))); }
  close(code = 1000, reason = '') {
    if (this.readyState !== WS_OPEN) return; const rb = Buffer.from(String(reason).slice(0,120)); const p = Buffer.alloc(2+rb.length);
    p.writeUInt16BE(code,0); rb.copy(p,2); this.socket.write(frame(0x8,p)); this.readyState=2; this.socket.end();
  }
  #onData(chunk) { if (this.readyState !== WS_OPEN) return; this.buffer = this.buffer.length ? Buffer.concat([this.buffer,chunk]) : Buffer.from(chunk); this.#parse(); }
  #parse() {
    while (this.buffer.length >= 2) {
      const b0=this.buffer[0], b1=this.buffer[1], fin=(b0&0x80)!==0, opcode=b0&0x0f, masked=(b1&0x80)!==0; let len=b1&0x7f, off=2;
      if (!fin) return this.close(1003,'fragmentation unsupported');
      if (len===126) { if(this.buffer.length<4)return; len=this.buffer.readUInt16BE(2); off=4; }
      else if (len===127) { if(this.buffer.length<10)return; const big=this.buffer.readBigUInt64BE(2); if(big>BigInt(this.maxPayload))return this.close(1009,'too large'); len=Number(big); off=10; }
      if (len>this.maxPayload) return this.close(1009,'too large'); if(!masked) return this.close(1002,'client frames must be masked'); if(this.buffer.length<off+4+len)return;
      const mask=this.buffer.subarray(off,off+4); off+=4; const payload=Buffer.from(this.buffer.subarray(off,off+len)); for(let i=0;i<payload.length;i++)payload[i]^=mask[i&3]; this.buffer=this.buffer.subarray(off+len);
      if(opcode===0x1)this.emit('message',payload); else if(opcode===0x8){this.close();return;} else if(opcode===0x9)this.socket.write(frame(0xA,payload)); else if(opcode!==0xA)return this.close(1003,'unsupported opcode');
    }
  }
}
function frame(opcode,payload){const len=payload.length;let h;if(len<126){h=Buffer.alloc(2);h[1]=len;}else if(len<=0xffff){h=Buffer.alloc(4);h[1]=126;h.writeUInt16BE(len,2);}else{h=Buffer.alloc(10);h[1]=127;h.writeBigUInt64BE(BigInt(len),2);}h[0]=0x80|opcode;return Buffer.concat([h,payload]);}
export function acceptWebSocket(req,socket,head,{path='/ws',maxPayload=16*1024}={}){
  const url=new URL(req.url,'http://localhost'); if(url.pathname!==path||req.headers.upgrade?.toLowerCase()!=='websocket'){socket.write('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n');socket.destroy();return null;}
  const key=req.headers['sec-websocket-key']; if(!key||req.headers['sec-websocket-version']!=='13'){socket.write('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');socket.destroy();return null;}
  const accept=crypto.createHash('sha1').update(key+GUID).digest('base64'); socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'+`Sec-WebSocket-Accept: ${accept}\r\n\r\n`); return new TinyWebSocket(socket,head,maxPayload);
}
