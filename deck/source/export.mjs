// Export the built deck: a PNG per step (QA + handout) and, with --video, an autoplay recording.
// Usage: node export.mjs [--video] [--steps 0,5,9] [--out dist]
import { mkdirSync, existsSync, renameSync, readdirSync, statSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const here = dirname(fileURLToPath(import.meta.url));
// Playwright comes from the PyRIT frontend's node_modules (same chromium build the repo tests use).
const playwrightRoot = process.env.PLAYWRIGHT_ROOT || 'C:/Users/spencersch/src/PyRIT.worktrees/pyrt-agent-execution-environment/frontend';
const { chromium } = createRequire(resolve(playwrightRoot, 'package.json'))('playwright');
const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : dflt; };
const out = resolve(here, opt('--out', 'dist'));
const indexUrl = pathToFileURL(resolve(out, 'index.html')).href;
const only = opt('--steps', null)?.split(',').map(Number);
const dwell = Number(opt('--dwell', 7000));

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, recordVideo: flag('--video') ? { dir: resolve(out, 'video-raw'), size: { width: 1920, height: 1080 } } : undefined });
const page = await context.newPage();
page.on('pageerror', (e) => console.error('pageerror', e.message));
page.on('console', (m) => { if (m.type() === 'error') console.error('console', m.text()); });
await page.goto(indexUrl + (flag('--present') ? '?mode=present' : ''));
await page.waitForFunction(() => window.__deck);
const total = await page.evaluate(() => window.__deck.flat.length);
const ids = await page.evaluate(() => window.__deck.flat.map(({ c, s }) => `${window.__deck.DECK.chapters[c].id}--${window.__deck.DECK.chapters[c].steps[s].id}`));
mkdirSync(resolve(out, 'png'), { recursive: true });

if (flag('--video')) {
  for (let i = 0; i < total; i++) {
    if (only && !only.includes(i)) continue;
    await page.evaluate((k) => window.__deck.go(k), i);
    await page.waitForTimeout(i === 0 ? 5000 : dwell);
  }
  await page.waitForTimeout(1500);
} else {
  for (let i = 0; i < total; i++) {
    if (only && !only.includes(i)) continue;
    await page.evaluate((k) => window.__deck.go(k), i);
    await page.waitForTimeout(2600);
    const file = resolve(out, 'png', `${String(i).padStart(2, '0')}-${ids[i]}.png`);
    await page.screenshot({ path: file });
    const overflow = await page.evaluate(() => {
      const bad = [];
      const frame = document.querySelector('#frame');
      const fr = frame.getBoundingClientRect();
      for (const sel of ['#lower', '#legend', '#captions', '#stageWrap']) { const n = document.querySelector(sel); if (n && n.getBoundingClientRect().bottom > fr.bottom + 1) bad.push(`${sel} overflows frame by ${Math.round(n.getBoundingClientRect().bottom - fr.bottom)}px`); }
      for (const a of document.querySelectorAll('.actor')) { if (a.scrollHeight > a.clientHeight + 2 && a.style.height) bad.push(`${a.dataset.id} clipped ${a.scrollHeight - a.clientHeight}px`); }
      const pre = document.querySelector('#evid pre'); if (pre && pre.scrollHeight > pre.clientHeight + 2) bad.push(`evidence clipped ${pre.scrollHeight - pre.clientHeight}px`);
      for (const v of document.querySelectorAll('.slot .v')) { if (v.scrollWidth > v.clientWidth + 2) bad.push(`slot ${v.parentElement.dataset.slot} overflows`); }
      return bad;
    });
    console.log(`${String(i).padStart(2, '0')} ${ids[i]}${overflow.length ? '  ⚠ ' + overflow.join('; ') : ''}`);
  }
}
await context.close();
await browser.close();
if (flag('--video')) {
  const dir = resolve(out, 'video-raw');
  const files = readdirSync(dir).filter((f) => f.endsWith('.webm')).map((f) => resolve(dir, f)).sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
  if (files[0]) { const target = resolve(out, `${opt('--name', 'walkthrough')}.webm`); if (existsSync(target)) renameSync(target, target + '.bak'); renameSync(files[0], target); console.log('video', target); }
}
