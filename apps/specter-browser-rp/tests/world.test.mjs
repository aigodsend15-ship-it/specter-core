import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { WorldStore } from '../src/persistence.mjs';

test('persistent world seeds NPCs and round-trips players/builds',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'specter-rp-'));const db=path.join(dir,'world.db');
  let s=new WorldStore(db);assert.equal(s.getNpcs().length,12);assert.equal(s.getBuildCount(),0);
  s.putPlayer({token:'token-123456789',name:'Tester',x:4,y:2,z:-7,yaw:1.2,money:900,mode:'car'});
  s.addBuild({id:'b1',owner:'Tester',kind:'wall',x:5,y:0,z:-8,yaw:0,created_at:1});
  s.setMeta('worldTime','13.5');s.close();
  s=new WorldStore(db);const p=s.getPlayer('token-123456789');assert.equal(p.name,'Tester');assert.equal(p.mode,'car');assert.equal(p.x,4);assert.equal(s.getBuilds()[0].kind,'wall');assert.equal(s.getMeta('worldTime'),'13.5');assert.equal(s.getNpcs().length,12);s.close();fs.rmSync(dir,{recursive:true,force:true});
});
