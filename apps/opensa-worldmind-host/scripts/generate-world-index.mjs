#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { createReadStream, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const root = resolve(process.argv[2] ?? '');
if (!process.argv[2]) {
  console.error('usage: node generate-world-index.mjs <game-folder> [output]');
  process.exit(2);
}

const output = resolve(process.argv[3] ?? join(root, '__index'));
const entries = [];

function walk(dir) {
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const absolute = join(dir, name.name);
    if (name.isDirectory()) {
      walk(absolute);
      continue;
    }
    if (!name.isFile()) continue;
    const path = relative(root, absolute).replaceAll('\\', '/');
    if (path === '__index' || path === '__index.sha256') continue;
    entries.push({ path, size: statSync(absolute).size });
  }
}

walk(root);
entries.sort((a, b) => a.path.localeCompare(b.path));

const json = `${JSON.stringify(entries, null, 2)}\n`;
writeFileSync(output, json);
const hash = createHash('sha256').update(json).digest('hex');
writeFileSync(`${output}.sha256`, `${hash}  ${output.split(/[\\/]/).at(-1)}\n`);

let total = 0;
for (const entry of entries) total += entry.size;
console.log(JSON.stringify({ event: 'world_index_ok', root, output, files: entries.length, bytes: total, sha256: hash }));
