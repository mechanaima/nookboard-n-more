// The Workspaces view's wording. The facts arrive from the server already
// computed (`app/workspace.py` decides what a folder is); these functions only
// decide how they read. Nothing here counts a file or ages a commit.

import test from "node:test";
import assert from "node:assert/strict";

import {
  branchLine, buttons, changedFiles, commitLine, languageLine, markerCount,
  markerLine, stateClass,
} from "./workspace.js";

const repo = {
  is_repo: true, has_commits: true, branch: "main", upstream: "Github/main",
  ahead: 2, behind: 3, age_text: "17 hours ago",
  last: { subject: "A note can say when" }, changed: [], untracked: [],
};

test("a workspace that wants you and one that does not look different", () => {
  assert.equal(stateClass({ is_repo: true, reasons: ["2 uncommitted"] }), "is-wanted");
  assert.equal(stateClass({ is_repo: true, reasons: [] }), "is-settled");
  assert.equal(stateClass({ is_repo: false }), "is-plain");
  assert.equal(stateClass({ missing: true }), "is-broken");
  // a git problem is broken, not "settled" -- it is not fine, it is unknown
  assert.equal(stateClass({ problem: "git is not installed" }), "is-broken");
});

test("the branch line carries the drift only when there is drift", () => {
  assert.equal(branchLine(repo), "main → Github/main ↑2 ↓3");
  // level with its upstream draws no arrows at all
  assert.equal(branchLine({ ...repo, ahead: 0, behind: 0 }), "main → Github/main");
});

test("no upstream is not the same as level with one", () => {
  // absent ahead/behind must not render as ↑0 ↓0 -- there is nowhere to push
  const local = { ...repo, upstream: null, ahead: null, behind: null };
  assert.equal(branchLine(local), "main");
});

test("a detached head says detached rather than naming a branch", () => {
  // A detached HEAD has no upstream to be ahead of, so the server reports no
  // drift -- and the line must not invent ↑0 ↓0 either.
  assert.equal(
    branchLine({ ...repo, detached: true, branch: null, upstream: null, ahead: null, behind: null }),
    "detached",
  );
});

test("a folder that is not a repo has no branch line at all", () => {
  assert.equal(branchLine({ is_repo: false }), "");
  assert.equal(commitLine({ is_repo: false }), "");
});

test("the commit line is the subject alone -- the age is the headline's job", () => {
  // The card already reads "12 uncommitted · 17 hours ago"; repeating the age on
  // the commit line would print one fact twice.
  assert.equal(commitLine(repo), "A note can say when");
  assert.equal(commitLine({ ...repo, last: { subject: "  " } }), "");
  // An empty tree says nothing here: the headline says "no commits yet".
  assert.equal(commitLine({ is_repo: true, has_commits: false, last: null }), "");
});

test("languages are listed biggest first and the tail is counted", () => {
  assert.equal(languageLine({ Python: 54, JavaScript: 25, Shell: 4, TOML: 1 }),
    "Python 54 · JavaScript 25 · Shell 4 +1 more");
  assert.equal(languageLine({ Shell: 1 }), "Shell 1");
  assert.equal(languageLine({}), "");
  assert.equal(languageLine(null), "");
});

test("the changed files are a capped list, and the cap is counted", () => {
  // Tracked and untracked together, because "what is uncommitted here" is one
  // question -- and each name is a place the card can open, which is why this
  // returns the list rather than a sentence about it.
  const state = { changed: ["a.py", "b.py"], untracked: ["c.py", "d.py"] };
  assert.deepEqual(changedFiles(state, 3), { files: ["a.py", "b.py", "c.py"], rest: 1 });
  assert.deepEqual(changedFiles(state, 99), {
    files: ["a.py", "b.py", "c.py", "d.py"], rest: 0,
  });
  // Nothing uncommitted is not a list of nothing: it is no list at all
  assert.deepEqual(changedFiles({}), { files: [], rest: 0 });
  assert.deepEqual(changedFiles({ changed: [], untracked: [] }), { files: [], rest: 0 });
  // A repo with a lot of work in it does not make 40 buttons
  const many = { changed: Array.from({ length: 40 }, (_, i) => `f${i}.py`), untracked: [] };
  const capped = changedFiles(many);
  assert.equal(capped.files.length, 6);
  assert.equal(capped.rest, 34);
});

test("a button is disabled when its tool is missing, and says which tool", () => {
  const rendered = buttons(repo, { editor: "/usr/bin/code", terminal: "", files: "/usr/bin/xdg-open" });
  const terminal = rendered.find((b) => b.what === "terminal");
  assert.equal(terminal.enabled, false);
  assert.match(terminal.why, /no terminal installed/);
  const editor = rendered.find((b) => b.what === "editor");
  assert.equal(editor.enabled, true);
  assert.match(editor.why, /code$/);
});

test("every button is refused when the folder is gone", () => {
  const rendered = buttons({ missing: true }, { editor: "/usr/bin/code" });
  assert.equal(rendered.every((b) => !b.enabled), true);
  assert.equal(rendered[0].why, "the folder is not there");
});

test("a marker reads as a place in the code first", () => {
  assert.equal(
    markerLine({ file: "app/main.py", line: 42, kind: "TODO", text: "wire the tray" }),
    "app/main.py:42  TODO wire the tray",
  );
  assert.equal(markerLine({ file: "x.sh", line: 3, kind: "FIXME", text: "" }),
    "x.sh:3  FIXME");
  assert.equal(markerLine(null), "");
});

test("marker counts read as English", () => {
  assert.equal(markerCount([]), "");
  assert.equal(markerCount([{ file: "a" }]), "1 marker");
  assert.equal(markerCount([{ file: "a" }, { file: "b" }]), "2 markers");
});
