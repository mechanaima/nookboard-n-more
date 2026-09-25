// node --test — the dashboard's first-open loading and offline states.
//
// renderHome() must: mark the board card busy the moment the fetch starts,
// clear it when the fetch lands, and — when the server cannot be reached —
// clear it *and* paint an explicit "not reachable" note instead of leaving the
// card blank. A blank card reads as an empty vault, which is the wrong thing to
// say when the truth is "the server is down".
//
// app.js boots itself on import, so globals are installed first and the module
// is imported dynamically. `api` is exported, and the test points its `home`
// method at a stub — the render helpers call `api.home(...)` directly.

import test from "node:test";
import assert from "node:assert/strict";

// --- A tiny element stub ---------------------------------------------------
class FakeEl {
  constructor(tag = "div") {
    this.tag = tag;
    this.textContent = "";
    this.className = "";
    this.value = "";
    this.hidden = false;
    this.innerHTML = "";
    this.disabled = false;
    this.dataset = {};
    this.children = [];
    this._classes = new Set();
    this.classList = {
      add: (...n) => { for (const c of n) this._classes.add(c); },
      remove: (...n) => { for (const c of n) this._classes.delete(c); },
      contains: (c) => this._classes.has(c),
      toggle: (c, force) => {
        const on = force === undefined ? !this._classes.has(c) : Boolean(force);
        if (on) this._classes.add(c); else this._classes.delete(c);
        return on;
      },
    };
    this.style = { setProperty() {} };
  }
  addEventListener() {}
  append(...nodes) { for (const n of nodes) this.children.push(n); }
  appendChild(n) { this.children.push(n); return n; }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  querySelectorAll() { return []; }
  setAttribute() {}
  focus() {}
  matches() { return false; }
}

// The two nodes renderHome specifically reaches for. Everything else the
// dashboard paints (…#home-vault, #home-stats) resolves to a throwaway stub.
const els = {};
for (const id of ["home-dash-status", "home-dash-body"]) els[id] = new FakeEl(id);

const fakeDocument = {
  querySelector(sel) {
    const id = typeof sel === "string" && sel.startsWith("#") ? sel.slice(1) : null;
    return (id && Object.prototype.hasOwnProperty.call(els, id)) ? els[id] : new FakeEl();
  },
  querySelectorAll() { return []; },
  createElement(tag) { return new FakeEl(tag); },
  addEventListener() {},
  body: new FakeEl("body"),
  activeElement: new FakeEl(),
  contains() { return false; },
};

function installGlobals() {
  globalThis.document = fakeDocument;
  globalThis.window = globalThis;
  globalThis.addEventListener = () => {};
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  globalThis.sessionStorage = { getItem: () => null, setItem: () => {} };
  Object.defineProperty(globalThis, "navigator", {
    value: { onLine: true }, writable: true, configurable: true,
  });
  globalThis.confirm = () => true;
  globalThis.fetch = async () => ({
    ok: true, status: 200,
    async text() { return "{}"; },
    async json() { return {}; },
  });
}

const HOME = {
  vault: "v",
  today: "2026-09-24",
  calendar: { label: "Sep 2026", year: 2026, month: 9, today: "2026-09-24" },
  statistics: { notes: 3, tasks: 1, tags: 2, collections: 2 },
  board: { ready: 0, open: 1, blocked: 0, done: 0 },
  today_card: { date: "2026-09-24", has_note: false, summarised: false, finished: 0, titles: [] },
  mood: { streak: 0, days_logged: 0, logged_today: false, pain: null, most_common: null },
  workspaces: { total: 0, line: "", workspaces: [] },
  streak: 0,
};

test("renderHome is busy while the first fetch is in flight", async () => {
  installGlobals();
  const mod = await import("./app.js");
  mod.state.activeView = "home";
  mod.state.homeMonth = undefined;
  mod.api.home = () => new Promise((resolve) => queueMicrotask(() => resolve(HOME)));

  const pending = mod.renderHome();
  // Called, not yet awaited: the fetch is in flight and both nodes are busy.
  assert.equal(els["home-dash-status"].classList.contains("is-busy"), true,
    "status line should be busy while the fetch is in flight");
  assert.equal(els["home-dash-body"].classList.contains("is-busy"), true,
    "dashboard body should be busy while the fetch is in flight");

  await pending;
  assert.equal(els["home-dash-status"].classList.contains("is-busy"), false,
    "busy flag should clear when the fetch lands");
  assert.equal(els["home-dash-body"].classList.contains("is-busy"), false,
    "dashboard body should clear when the fetch lands");
});

test("renderHome clears the busy flag once the fetch lands", async () => {
  installGlobals();
  const mod = await import("./app.js");
  mod.state.activeView = "home";
  mod.state.homeMonth = undefined;
  mod.api.home = async () => HOME;

  await mod.renderHome();

  assert.equal(els["home-dash-status"].classList.contains("is-busy"), false,
    "busy flag should be cleared once the fetch lands");
  assert.equal(els["home-dash-body"].classList.contains("is-busy"), false,
    "dashboard body should be cleared once the fetch lands");
});

test("renderHome clears the busy flag and says so when the server is unreachable", async () => {
  installGlobals();
  const mod = await import("./app.js");
  mod.state.activeView = "home";
  mod.state.homeMonth = undefined;
  mod.api.home = async () => { throw new Error("connect ECONNREFUSED"); };
  // Start from a painted body so we prove the stale content is replaced.
  els["home-dash-body"].replaceChildren(new FakeEl("p"));

  await mod.renderHome();

  assert.equal(els["home-dash-status"].classList.contains("is-busy"), false,
    "busy flag should be cleared after an unreachable server");
  assert.equal(els["home-dash-body"].classList.contains("is-busy"), false,
    "dashboard body should be cleared after an unreachable server");
  assert.match(els["home-dash-status"].textContent, /not reachable/i,
    "the status line should say the server could not be reached");
  assert.equal(els["home-dash-body"].children.length, 1,
    "the body should hold exactly the offline note");
  assert.match(els["home-dash-body"].children[0].textContent, /not reachable/i,
    "an explicit offline note should be painted, never a blank body");
});
