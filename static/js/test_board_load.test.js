// node --test — what the board shows when the read fails.
//
// A failed *refresh* must not leave the cards from the last good load standing
// in the grid. After a filter change they read as the results of the request
// that just failed, which is the one thing a filtered board must not imply. The
// grid is emptied and says so; the failure itself is already on the status line
// and in the summary line.
//
// app.js boots itself on import, so globals are installed first and the module
// is imported dynamically — the same harness the dashboard's test uses.

import test from "node:test";
import assert from "node:assert/strict";

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

// The nodes renderBoard reaches for before and after the fetch. Anything else
// it paints on a good load is not on this path.
const els = {};
for (const id of ["board-columns", "board-view", "board-summary", "board-status"]) {
  els[id] = new FakeEl(id);
}

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

test("a board read that fails empties the grid rather than leaving the old cards", async () => {
  installGlobals();
  const mod = await import("./app.js");
  mod.state.activeView = "board";
  // A grid still holding the last good load's two columns.
  els["board-columns"].replaceChildren(new FakeEl("section"), new FakeEl("section"));
  mod.api.board = async () => { throw new Error("connect ECONNREFUSED"); };

  await mod.renderBoard();

  assert.equal(els["board-columns"].children.length, 1,
    "the stale columns should be replaced by one line saying the read failed");
  assert.match(els["board-columns"].children[0].textContent, /could not be read/i,
    "and the grid should say why it is empty");
  assert.equal(els["board-columns"].classList.contains("is-loading"), false,
    "the loading state should clear even when the read fails");
  assert.equal(els["board-view"].classList.contains("is-loading"), false,
    "the view's loading state should clear too, or the skeleton never goes");
  assert.match(els["board-status"].textContent, /Could not read the board/,
    "the status line should name the failure");
  assert.match(els["board-summary"].textContent, /ECONNREFUSED/,
    "the summary line should carry the message itself");
});
