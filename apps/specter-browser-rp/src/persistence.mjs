import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';

export class PlayerStore {
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
        y REAL NOT NULL DEFAULT 0,
        z REAL NOT NULL DEFAULT 0,
        yaw REAL NOT NULL DEFAULT 0,
        money INTEGER NOT NULL DEFAULT 500,
        updated_at INTEGER NOT NULL
      );
    `);
    this.getStmt = this.db.prepare('SELECT * FROM players WHERE token = ?');
    this.putStmt = this.db.prepare(`
      INSERT INTO players(token,name,x,y,z,yaw,money,updated_at)
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(token) DO UPDATE SET
        name=excluded.name,x=excluded.x,y=excluded.y,z=excluded.z,
        yaw=excluded.yaw,money=excluded.money,updated_at=excluded.updated_at
    `);
  }

  get(token) {
    return this.getStmt.get(token) ?? null;
  }

  put(player) {
    this.putStmt.run(
      player.token, player.name, player.x, player.y, player.z,
      player.yaw, player.money, Date.now()
    );
  }

  close() { this.db.close(); }
}
