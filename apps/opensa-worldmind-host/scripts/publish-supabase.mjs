#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';

const sourceDir = resolve(process.argv[2] ?? '');
if (!process.argv[2]) {
  console.error('usage: node publish-supabase.mjs <fetch-pack-dir> [remote-prefix]');
  process.exit(2);
}

const supabaseUrl = mustEnv('SUPABASE_URL').replace(/\/$/, '');
const serviceKey = mustEnv('SUPABASE_SERVICE_ROLE_KEY');
const bucket = process.env.SUPABASE_PUBLISH_BUCKET || 'specter-public';
const prefix = trimSlashes(process.argv[3] || 'games/specter-current');
const concurrency = clamp(Number(process.env.SPECTER_UPLOAD_CONCURRENCY || 2), 1, 4);
const maxObjectBytes = Number(process.env.SPECTER_MAX_OBJECT_BYTES || 49 * 1024 * 1024);

const manifestPath = join(sourceDir, 'manifest.json');
const manifestBytes = readFileSync(manifestPath);
const manifest = JSON.parse(manifestBytes.toString('utf8'));
const expected = new Map();
for (const group of Object.values(manifest.chunks ?? {})) {
  for (const chunk of group) expected.set(chunk.file, chunk);
}

const files = readdirSync(sourceDir)
  .filter((name) => name.endsWith('.zip'))
  .sort();

if (files.length !== expected.size) {
  throw new Error(`manifest/files mismatch: ${expected.size} chunks declared, ${files.length} zip files present`);
}

for (const file of files) {
  const meta = expected.get(file);
  if (!meta) throw new Error(`undeclared chunk ${file}`);
  const absolute = join(sourceDir, file);
  const bytes = statSync(absolute).size;
  if (bytes !== meta.bytes) throw new Error(`byte mismatch for ${file}: manifest=${meta.bytes}, disk=${bytes}`);
  if (bytes > maxObjectBytes) {
    throw new Error(`${file} is ${bytes} bytes; exceeds configured storage ceiling ${maxObjectBytes}`);
  }
  const hash = createHash('sha1').update(readFileSync(absolute)).digest('hex').slice(0, 12);
  if (hash !== meta.hash || !file.includes(hash)) throw new Error(`content hash mismatch for ${file}`);
}

const manifestSha256 = createHash('sha256').update(manifestBytes).digest('hex');
console.log(JSON.stringify({
  event: 'worldcloud_publish_plan',
  bucket,
  prefix,
  chunks: files.length,
  manifestSha256,
  maxObjectBytes,
  concurrency,
}));

// Two-phase publication: immutable content-addressed chunks first, mutable manifest last.
// A browser can therefore never observe a manifest pointing at chunks that were not uploaded yet.
await mapLimit(files, concurrency, async (file) => {
  const bytes = readFileSync(join(sourceDir, file));
  await upload(`${prefix}/${file}`, bytes, 'application/zip', '31536000', false);
});

await upload(`${prefix}/manifest.json`, manifestBytes, 'application/json', '0', true);

const publicManifestUrl = `${supabaseUrl}/storage/v1/object/public/${encodeURIComponent(bucket)}/${encodePath(`${prefix}/manifest.json`)}`;
console.log(JSON.stringify({
  event: 'worldcloud_publish_ok',
  bucket,
  prefix,
  chunks: files.length,
  manifestSha256,
  publicManifestUrl,
}));

async function upload(objectPath, bytes, contentType, cacheControl, upsert) {
  const url = `${supabaseUrl}/storage/v1/object/${encodeURIComponent(bucket)}/${encodePath(objectPath)}`;
  const headers = {
    apikey: serviceKey,
    Authorization: `Bearer ${serviceKey}`,
    'Content-Type': contentType,
    'cache-control': `max-age=${cacheControl}`,
    'x-upsert': upsert ? 'true' : 'false',
  };

  if (!upsert) {
    const publicUrl = `${supabaseUrl}/storage/v1/object/public/${encodeURIComponent(bucket)}/${encodePath(objectPath)}`;
    try {
      const head = await fetch(publicUrl, { method: 'HEAD', cache: 'no-store' });
      if (head.ok) {
        console.log(JSON.stringify({ event: 'worldcloud_upload_skip', object: objectPath, reason: 'already_exists' }));
        return;
      }
    } catch {
      // A transient HEAD failure is not authoritative; continue to the authenticated upload path.
    }
  }

  let last = null;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const response = await fetch(url, { method: 'POST', headers, body: bytes });
      if (response.ok) {
        console.log(JSON.stringify({ event: 'worldcloud_upload_ok', object: objectPath, bytes: bytes.byteLength }));
        return;
      }
      const text = await response.text();
      if (!upsert && response.status === 409) {
        console.log(JSON.stringify({ event: 'worldcloud_upload_skip', object: objectPath, reason: 'conflict_exists' }));
        return;
      }
      last = new Error(`upload ${objectPath} failed ${response.status}: ${text.slice(0, 300)}`);
      if (![408, 425, 429, 500, 502, 503, 504].includes(response.status)) throw last;
    } catch (error) {
      last = error;
    }
    await sleep(Math.min(8000, 500 * 2 ** attempt) + Math.floor(Math.random() * 200));
  }
  throw last ?? new Error(`upload failed: ${objectPath}`);
}

async function mapLimit(items, limit, fn) {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) {
      const index = cursor++;
      if (index >= items.length) return;
      await fn(items[index]);
    }
  });
  await Promise.all(workers);
}

function encodePath(path) {
  return path.split('/').map(encodeURIComponent).join('/');
}

function trimSlashes(value) {
  return value.replace(/^\/+|\/+$/g, '');
}

function mustEnv(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function clamp(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, Math.trunc(value)));
}

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}
