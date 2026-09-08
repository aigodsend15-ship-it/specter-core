import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';

const NPC_SEED = [
  ['Maya', -32, -22, 'builder'], ['Dante', 24, -35, 'mechanic'],
  ['Luna', 38, 18, 'explorer'], ['Ravi', -45, 31, 'trader'],
  ['Niko', 8, 42, 'builder'], ['Zoe', -12, 52, 'artist'],
  ['Kai', 56, -8, 'runner'], ['Iris', -58, -44, 'gardener'],
  ['Theo', 72, 34, 'mechanic'], ['Sora', -76, 18, 'explorer'],
  ['Juno', 18, -74, 'trader'], ['Atlas', -4, 76, 'builder']
];

export class WorldStore {
  constructor(filename) {
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    this.db = new DatabaseSync(filename);
    this.db.exec(`
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=NORMAL;
      CREATE TABLE IF NOT EXISTS players (
        token TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        x REAL NOT NULL DEFAULT 0,
        y REAL NOT NULL DEFAULT 1.2,
        z REAL NOT NULL DEFAULT 0,
        yaw REAL NOT NULL DEFAULT 0,
        money INTEGER NOT NULL DEFAULT 500,
        mode TEXT NOT NULL DEFAULT 'foot',
        updated_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS npcs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        x REAL NOT NULL,
        z REAL NOT NULL,
        yaw REAL NOT NULL DEFAULT 0,
        energy REAL NOT NULL DEFAULT 100,
        materials REAL NOT NULL DEFAULT 0,
        credits INTEGER NOT NULL DEFAULT 100,
        role TEXT NOT NULL,
        goal TEXT NOT NULL DEFAULT 'explore',
        memory TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS builds (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        kind TEXT NOT NULL,
        x REAL NOT NULL,
        y REAL NOT NULL,
        z REAL NOT NULL,
        yaw REAL NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS world_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );
    `);
    for (const sql of [
      "ALTER TABLE players ADD COLUMN y REAL NOT NULL DEFAULT 1.2",
      "ALTER TABLE players ADD COLUMN mode TEXT NOT NULL DEFAULT 'foot'"
    ]) { try { this.db.exec(sql); } catch {} }

    this.getPlayerStmt = this.db.prepare('SELECT * FROM players WHERE token = ?');
    this.putPlayerStmt = this.db.prepare(`
      INSERT INTO players(token,name,x,y,z,yaw,money,mode,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?)
      ON CONFLICT(token) DO UPDATE SET name=excluded.name,x=excluded.x,y=excluded.y,z=excluded.z,
        yaw=excluded.yaw,money=excluded.money,mode=excluded.mode,updated_at=excluded.updated_at
    `);
    this.putNpcStmt = this.db.prepare(`
      INSERT INTO npcs(id,name,x,z,yaw,energy,materials,credits,role,goal,memory,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET x=excluded.x,z=excluded.z,yaw=excluded.yaw,
        energy=excluded.energy,materials=excluded.materials,credits=excluded.credits,
        goal=excluded.goal,memory=excluded.memory,updated_at=excluded.updated_at
    `);
    this.addBuildStmt = this.db.prepare(`
      INSERT OR IGNORE INTO builds(id,owner,kind,x,y,z,yaw,created_at) VALUES(?,?,?,?,?,?,?,?)
    `);
    this.seedNpcs();
    if (!this.getMeta('worldTime')) this.setMeta('worldTime', '9.0');
  }

  seedNpcs() {
    const count = Number(this.db.prepare('SELECT COUNT(*) AS n FROM npcs').get().n);
    if (count) return;
    const ins = this.db.prepare(`INSERT INTO npcs(id,name,x,z,yaw,energy,materials,credits,role,goal,memory,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`);
    const now = Date.now();
    NPC_SEED.forEach(([name,x,z,role], i) => ins.run(
      `npc-${i+1}`, name, x, z, 0, 82 + (i%4)*4, (i%3)*2, 100 + i*7,
      role, role === 'builder' ? 'gather' : 'explore', `${name} acordou em West Coast.`, now
    ));
  }

  getPlayer(token) { return this.getPlayerStmt.get(token) ?? null; }
  putPlayer(p) {
    this.putPlayerStmt.run(p.token,p.name,p.x,p.y ?? 1.2,p.z,p.yaw,p.money,p.mode ?? 'foot',Date.now());
  }
  getNpcs() { return this.db.prepare('SELECT * FROM npcs ORDER BY id').all().map(x => ({...x})); }
  putNpc(n) {
    this.putNpcStmt.run(n.id,n.name,n.x,n.z,n.yaw,n.energy,n.materials,n.credits,n.role,n.goal,n.memory,Date.now());
  }
  getBuilds(limit=250) { return this.db.prepare('SELECT * FROM builds ORDER BY created_at ASC LIMIT ?').all(limit).map(x=>({...x})); }
  addBuild(b) { this.addBuildStmt.run(b.id,b.owner,b.kind,b.x,b.y,b.z,b.yaw,b.created_at ?? Date.now()); }
  getBuildCount() { return Number(this.db.prepare('SELECT COUNT(*) AS n FROM builds').get().n); }
  getMeta(key) { return this.db.prepare('SELECT value FROM world_meta WHERE key=?').get(key)?.value ?? null; }
  setMeta(key,value) { this.db.prepare('INSERT INTO world_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key,String(value)); }
  close() { this.db.close(); }
}
