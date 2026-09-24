// A name exported by a sibling module that this file *uses* must be imported.
//
// This exists because the failure is invisible until the exact code path runs:
// `isOpenTask` was called in app.js's dependency picker without being imported,
// so renderDeps threw a ReferenceError in the browser — while `node --check`
// passed, the unit tests passed (board.test.js imports it from the module
// directly, so it never exercises app.js's binding), and the DOM assertions
// passed (they check that the panel exists, not that it rendered). Wiring the
// browser is the slowest place to discover it, so it is checked here instead.
//
// Note the direction: the obvious guard — "does every imported name exist?" —
// does NOT catch this, because a name that was never imported is never checked.
// The check has to run from the module's exports towards this file's usage.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

const MODULES = [
  "app.js",
  "entry.js",
  "board.js",
  "mood.js",
  "rapid.js",
  "calendar.js",
  "wikilink.js",
];

function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");
}

function localImports(source) {
  const out = new Map(); // module path -> Set of imported names
  const re = /import\s*\{([^}]+)\}\s*from\s*["'](\.\/[^"']+)["']/g;
  for (const m of source.matchAll(re)) {
    const names = m[1]
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => s.split(/\s+as\s+/)[0].trim());
    const set = out.get(m[2]) ?? new Set();
    for (const n of names) set.add(n);
    out.set(m[2], set);
  }
  return out;
}

function exportsOf(source) {
  const names = new Set();
  for (const m of source.matchAll(/export\s+(?:async\s+)?function\s+([A-Za-z0-9_$]+)/g)) {
    names.add(m[1]);
  }
  for (const m of source.matchAll(/export\s+(?:const|let|var)\s+([A-Za-z0-9_$]+)/g)) {
    names.add(m[1]);
  }
  for (const m of source.matchAll(/export\s*\{([^}]+)\}/g)) {
    for (const part of m[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/).pop().trim();
      if (name) names.add(name);
    }
  }
  return names;
}

/** Names a module exports that `source` uses without importing. */
function usedButNotImported(source, exportedBy) {
  const body = stripComments(source);
  const imported = localImports(source);
  const missing = [];
  for (const [modulePath, names] of Object.entries(exportedBy)) {
    const have = imported.get(modulePath) ?? new Set();
    for (const name of names) {
      if (have.has(name)) continue;
      const used = new RegExp(`(?<![.\\w$])${name}\\s*[(.,)\\[\\]]`).test(body);
      if (used) missing.push(`${name} (used here, exported by ${modulePath})`);
    }
  }
  return missing;
}

test("the check catches a used-but-not-imported name", () => {
  // Proving the guard fails on the bug it was written for. Without this the
  // check could pass vacuously and read as coverage.
  const exported = { "./board.js": new Set(["isOpenTask", "STAGES"]) };
  const broken = [
    'import { STAGES } from "./board.js";',
    "const list = tasks.filter(isOpenTask);",
  ].join("\n");
  assert.deepEqual(usedButNotImported(broken, exported), [
    "isOpenTask (used here, exported by ./board.js)",
  ]);
  // ...and that a correct file is not flagged.
  const fine = [
    'import { STAGES, isOpenTask } from "./board.js";',
    "const list = tasks.filter(isOpenTask);",
  ].join("\n");
  assert.deepEqual(usedButNotImported(fine, exported), []);
});

for (const file of MODULES) {
  test(`${file} imports every sibling module name it uses`, () => {
    const source = readFileSync(join(here, file), "utf8");
    const exportedBy = {};
    for (const modulePath of localImports(source).keys()) {
      const target = modulePath.replace(/^\.\//, "");
      exportedBy[modulePath] = exportsOf(readFileSync(join(here, target), "utf8"));
    }
    // Non-vacuity: a file with no local imports must not silently "pass".
    assert.ok(Object.keys(exportedBy).length > 0 || file !== "app.js");
    assert.deepEqual(usedButNotImported(source, exportedBy), []);
  });
}
