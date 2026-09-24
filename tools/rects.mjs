// Report where elements are on the page so a screenshot can be sampled at
// exactly those pixels. Coordinates come from the live DOM because guessing
// them from an image is how you end up measuring empty panel and reporting a
// confident, wrong number.
//
// usage: node tools/rects.mjs <url> <port> <selector>
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [url = 'http://127.0.0.1:8765/', port = '9330', selector = 'body'] = process.argv.slice(2);
const chrome = process.env.CHROME
  || ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome'].find(Boolean);
const profile = mkdtempSync(join(tmpdir(), 'nookboard-rects-'));
const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', '--disable-gpu',
  '--window-size=1600,980', url], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  let wsUrl;
  for (let i = 0; i < 60 && !wsUrl; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      wsUrl = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl)?.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    if (!wsUrl) await sleep(250);
  }
  if (!wsUrl) throw new Error('no devtools target');
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  await new Promise((r) => ws.addEventListener('open', r));
  const send = (method, params = {}) => new Promise((res) => {
    const n = ++id; pending.set(n, res); ws.send(JSON.stringify({ id: n, method, params }));
  });
  await sleep(2200); // let the view render
  const r = await send('Runtime.evaluate', {
    expression: `(() => {
      const out = [];
      for (const el of document.querySelectorAll(${JSON.stringify(selector)})) {
        const b = el.getBoundingClientRect();
        if (b.width < 4 || b.height < 4) continue;
        const cs = getComputedStyle(el);
        out.push({ x: Math.round(b.x), y: Math.round(b.y),
                   w: Math.round(b.width), h: Math.round(b.height),
                   text: el.textContent.trim().slice(0, 24),
                   color: cs.color, bg: cs.backgroundColor, border: cs.borderTopColor });
      }
      return out;
    })()`, returnByValue: true,
  });
  console.log(JSON.stringify(r.result.result.value ?? []));
  ws.close();
  proc.kill();
}

main().catch((e) => { console.error('rects:', e.message); proc.kill(); process.exit(1); });
