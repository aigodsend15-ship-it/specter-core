import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const hostRoot = resolve(here, '..');
const dist = join(hostRoot, '.opensa', 'dist');

const runtime = String.raw`
<script id="specter-direct-launch">
(() => {
  const text = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const buttons = () => [...document.querySelectorAll('button,a,[role="button"]')];
  const find = (parts) => buttons().find((el) => parts.some((p) => text(el).includes(p)));
  const hideDeadDemo = () => {
    for (const el of buttons()) {
      if (text(el).includes('gostown')) {
        const row = el.closest('li,div');
        if (row && /temporarily unavailable/i.test(row.textContent || '')) row.style.display = 'none';
      }
    }
  };
  let gameClicked = false;
  const advance = () => {
    hideDeadDemo();
    if (new URLSearchParams(location.search).get('menu') === '1') return;
    if (!gameClicked) {
      const game = find(['run gta san andreas', 'run san andreas', 'specter gta rp']);
      if (game) {
        gameClicked = true;
        game.click();
      }
    }
    // Deliberately never call showDirectoryPicker() from a synthetic click. Chromium requires
    // a real user gesture. Once cloud assets are configured this picker path is bypassed entirely.
  };
  const observer = new MutationObserver(() => advance());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(advance, 250);
  setTimeout(advance, 1200);
  setTimeout(advance, 3000);
})();
</script>`;

for (const file of [join(dist, 'index.html'), join(dist, 'legacy', 'index.html')]) {
  if (!existsSync(file)) throw new Error(`missing built launcher: ${file}`);
  let html = readFileSync(file, 'utf8');
  if (!html.includes('specter-direct-launch')) {
    html = html.includes('</body>') ? html.replace('</body>', `${runtime}\n</body>`) : `${html}\n${runtime}`;
  }
  writeFileSync(file, html);
  console.log(JSON.stringify({ event: 'specter_direct_launch_injected', file }));
}
