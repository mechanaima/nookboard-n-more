#!/usr/bin/env node
// check-scroll.mjs -- every view must be able to reach its own bottom.
//
// The app hides the page scroll on a desktop (`html, body { overflow: hidden }`,
// section 1 of app.css); only below 860px does the responsive block hand scrolling
// back to the body. So a view whose content is taller than the window has to scroll
// *itself*, and a view that does not is a view with content you cannot get to --
// which is what the History view was: 28 rows, the last of them below the fold,
// nothing to scroll. At 800px wide the bug does not exist, which is why a check has
// to run at a desktop size to see it at all.
//
// This asks every view the question the bug report asked, in the same terms:
// is there a scroller in my ancestry, or does the content fit?
//
// usage: node tools/check-scroll.mjs [url] [--seed N]
//        node tools/check-scroll.mjs http://127.0.0.1:8765/
//
// Without --seed this can only prove that nothing overflows: a view with little in
// it fits, and a view with little in it cannot be caught failing to scroll. `--seed N`
// posts N notes through the API (and deletes them again) so the tall views are
// genuinely tall -- and in a vault with history on, each note and each deletion is
// also a change, which is what makes the History view long. **Point --seed at a
// scratch vault**: it writes to the vault, and the history it creates is permanent.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const argv = process.argv.slice(2);
const seedFlag = argv.indexOf("--seed");
const SEED = seedFlag === -1 ? 0 : Number(argv[seedFlag + 1] || 0);
const PAGE = argv.find((a) => a.startsWith("http")) || "http://127.0.0.1:8765/";
const SEED_IDS = Array.from({ length: SEED }, (_, i) => `scrollcheck-${String(i).padStart(3, "0")}`);
const PORT = Number(process.env.CHECK_SCROLL_PORT || 9699);
const WIDTH = 1680;
const HEIGHT = 1000;
// A view needs a moment to fetch its data before its height means anything, and one
// job at a time means the transcribe view is slow to settle.
const SETTLE_MS = 1800;

const profile = mkdtempSync(join(tmpdir(), "nookboard-scroll-"));
const chromium = spawn(
  "chromium",
  [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    `--window-size=${WIDTH},${HEIGHT}`,
    "--force-device-scale-factor=1",
    PAGE,
  ],
  { stdio: "ignore" },
);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function pageTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch {
      // not listening yet
    }
    await sleep(250);
  }
  throw new Error("chromium never offered a page target");
}

class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.next = 1;
    this.pending = new Map();
    socket.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      const resolve = this.pending.get(msg.id);
      if (resolve) {
        this.pending.delete(msg.id);
        resolve(msg);
      }
    });
  }
  send(method, params = {}) {
    const id = this.next++;
    return new Promise((resolve) => {
      this.pending.set(id, resolve);
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const msg = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (msg.result?.exceptionDetails) {
      throw new Error(
        `evaluate threw: ${JSON.stringify(msg.result.exceptionDetails).slice(0, 400)}`,
      );
    }
    return msg.result?.result?.value;
  }
}

// Defined once in the page, in two halves on purpose. Showing a view and measuring
// it must not happen in the same tick: most views fetch their data when they render,
// so a measure taken in the click's own tick reads an empty view -- 857 tall, nothing
// inside, and "fits" for every view including the broken one. That is exactly the
// mistake this check was written to catch, made by the check itself.
const MEASURE = `
window.__nookShow = (view) => {
  const tab = document.querySelector('.tab[data-view="' + view + '"]');
  if (tab) tab.click();
  return true;
};
window.__nookReachable = (view) => {
  const el = document.getElementById(view + '-view') || document.getElementById(view + '-pane');
  if (!el) return JSON.stringify({ view, error: 'no element' });
  let node = el, scroller = null, depth = 0;
  while (node && depth < 6) {
    const overflowY = getComputedStyle(node).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') { scroller = node; break; }
    node = node.parentElement;
    depth++;
  }
  const viewport = window.innerHeight;
  const bottom = el.getBoundingClientRect().bottom;
  const canScroll = Boolean(scroller && scroller.scrollHeight > scroller.clientHeight + 2);
  const pageScrolls = getComputedStyle(document.body).overflowY === 'auto' ||
    document.documentElement.scrollHeight > window.innerHeight;
  return JSON.stringify({
    view,
    scroller: scroller ? (scroller.id || scroller.className).toString().slice(0, 30) : 'NONE',
    contentBottom: Math.round(bottom),
    viewport,
    slack: scroller ? Math.round(scroller.scrollHeight - scroller.clientHeight) : 0,
    overflows: bottom > viewport + 2,
    reachable: bottom <= viewport + 2 || canScroll || pageScrolls,
  });
};
true;`;

if (SEED) {
  console.log(`seeding ${SEED} notes through the API (this writes to the vault)...`);
  for (const id of SEED_IDS) {
    await fetch(new URL("/api/notes", PAGE), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        id,
        collection: "inbox",
        title: `Scroll check ${id.slice(-3)}`,
        signifier: "note",
        status: "open",
      }),
    });
  }
  console.log("  seeded.");
}

const wsUrl = await pageTarget();
const socket = new WebSocket(wsUrl);
await new Promise((resolve) => socket.addEventListener("open", resolve));
const cdp = new Cdp(socket);
await cdp.send("Runtime.enable");
await sleep(2500); // the app boots, then the first view renders
await cdp.evaluate(MEASURE);

const tabs = JSON.parse(
  await cdp.evaluate(
    `JSON.stringify([...document.querySelectorAll('.tab[data-view]')].map((t) => t.dataset.view))`,
  ),
);

let broken = 0;
let tall = 0;
const rows = [];
for (const view of tabs) {
  await cdp.evaluate(`window.__nookShow("${view}")`);
  await sleep(SETTLE_MS);
  const settled = JSON.parse(await cdp.evaluate(`window.__nookReachable("${view}")`));
  if (settled.error) {
    console.log(`  ??      ${view}: ${settled.error}`);
    continue;
  }
  // `overflows` is false for a view that scrolls correctly (its own height is
  // clamped), so a scroller with slack is the honest sign of content to scroll.
  if (settled.overflows || settled.slack > 0) tall++;
  if (!settled.reachable) broken++;
  rows.push(settled);
  console.log(
    `  ${settled.reachable ? "ok  " : "FAIL"}    ${settled.view.padEnd(12)} ` +
      `scroller=${settled.scroller.padEnd(16)} bottom=${settled.contentBottom} ` +
      `viewport=${settled.viewport}${settled.slack ? ` slack=${settled.slack}` : ""}`,
  );
}

if (SEED) {
  for (const id of SEED_IDS) {
    await fetch(new URL(`/api/notes/${id}`, PAGE), { method: "DELETE" });
  }
  console.log(`\nremoved the ${SEED} seeded notes.`);
}

socket.close();
chromium.kill();
await sleep(150);
rmSync(profile, { recursive: true, force: true });

console.log(`\n${rows.length} views · ${broken} unreachable · ${tall} taller than the window`);
if (!tall) {
  console.log(
    "note: no view was taller than the window, so this run proves only that nothing " +
      "overflows -- it cannot catch a missing scroller without content to overflow.",
  );
}
if (broken) {
  console.log(
    "\nA view must declare `min-height: 0; overflow-y: auto` (like .workspaces-view and\n" +
      ".history-view do) or the app's hidden page scroll leaves its lower half unreachable.",
  );
  process.exit(1);
}
process.exit(0);
