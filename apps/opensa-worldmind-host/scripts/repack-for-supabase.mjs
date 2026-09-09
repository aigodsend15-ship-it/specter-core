#!/usr/bin/env node
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const repo = resolve(process.argv[2] ?? '');
const buildDir = resolve(process.argv[3] ?? '');
const outRoot = resolve(process.argv[4] ?? join(buildDir || '.', 'opensa-pack'));
if (!process.argv[2] || !process.argv[3]) {
  console.error('usage: node repack-for-supabase.mjs <opensa-repo> <pmb-build-dir> [out-root]');
  process.exit(2);
}

const chunkFile = join(repo, 'tools', 'fetch-pack', 'src', 'chunk.ts');
const original = readFileSync(chunkFile, 'utf8');
const maxObjectBytes = Number(process.env.SPECTER_MAX_OBJECT_BYTES || 49 * 1024 * 1024);
const candidatesMiB = (process.env.SPECTER_CHUNK_TARGETS_MIB || '36,32,28,24,20')
  .split(',')
  .map((value) => Number(value.trim()))
  .filter((value) => Number.isFinite(value) && value > 0);

let winner = null;
try {
  for (const targetMiB of candidatesMiB) {
    const targetBytes = Math.trunc(targetMiB * 1024 * 1024);
    const patched = original.replace(
      /export const TARGET_CHUNK_BYTES = \d+ \* 1024 \* 1024;/,
      `export const TARGET_CHUNK_BYTES = ${targetBytes}; // SPECTER WorldCloud adaptive target`,
    );
    if (patched === original) throw new Error('OpenSA TARGET_CHUNK_BYTES anchor not found');
    writeFileSync(chunkFile, patched);

    run(repo, [
      join(repo, 'node_modules', 'tsx', 'dist', 'cli.mjs'),
      join(repo, 'tools', 'fetch-pack', 'src', 'cli.ts'),
      '--build', buildDir,
      '--out', outRoot,
    ]);

    const packDir = findPackDir(outRoot);
    const manifest = JSON.parse(readFileSync(join(packDir, 'manifest.json'), 'utf8'));
    const chunks = Object.values(manifest.chunks ?? {}).flat();
    const oversize = chunks.filter((chunk) => chunk.bytes > maxObjectBytes);
    const maxBytes = chunks.reduce((max, chunk) => Math.max(max, chunk.bytes), 0);
    console.log(JSON.stringify({
      event: 'worldcloud_repack_attempt',
      targetMiB,
      targetBytes,
      chunks: chunks.length,
      maxBytes,
      maxObjectBytes,
      oversize: oversize.map((chunk) => ({ file: chunk.file, bytes: chunk.bytes })),
    }));
    if (oversize.length === 0) {
      winner = { packDir, targetMiB, targetBytes, maxBytes, chunks: chunks.length };
      break;
    }
  }
} finally {
  writeFileSync(chunkFile, original);
}

if (!winner) {
  throw new Error(`could not produce a pack below ${maxObjectBytes} bytes/object; lower SPECTER_CHUNK_TARGETS_MIB`);
}
console.log(JSON.stringify({ event: 'worldcloud_repack_ok', ...winner, maxObjectBytes }));

function run(cwd, args) {
  const executable = process.execPath;
  const result = spawnSync(executable, args, { cwd, env: process.env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`fetch-pack failed with exit ${result.status}`);
}

function findPackDir(root) {
  const candidates = readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => join(root, entry.name))
    .filter((dir) => {
      try { return statSync(join(dir, 'manifest.json')).isFile(); } catch { return false; }
    })
    .sort((a, b) => statSync(join(b, 'manifest.json')).mtimeMs - statSync(join(a, 'manifest.json')).mtimeMs);
  if (candidates.length === 0) throw new Error(`no fetch-pack manifest found under ${root}`);
  return candidates[0];
}
