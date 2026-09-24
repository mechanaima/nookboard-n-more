// Finding query blocks in a note body, and splicing answers back in.

import assert from "node:assert/strict";
import test from "node:test";

import { splitQueries, spliceQueries } from "./query.js";

const BODY = "Before\n\n```nookboard\ncompleted this week\n```\n\nAfter\n";

test("a query block is found with its query text", () => {
  const found = splitQueries(BODY);
  assert.equal(found.length, 1);
  assert.equal(found[0].text, "completed this week");
  assert.deepEqual(BODY.split("\n").slice(found[0].open, found[0].close + 1), [
    "```nookboard",
    "completed this week",
    "```",
  ]);
});

test("an answer is spliced in place of the query", () => {
  const out = spliceQueries(BODY, () => "- [[Fix printer]]");
  assert.equal(out, "Before\n\n- [[Fix printer]]\n\nAfter\n");
});

test("an unanswered query is left exactly as written", () => {
  // undefined means "not known yet": showing the query beats showing a gap.
  assert.equal(spliceQueries(BODY, () => undefined), BODY);
});

test("a fence in another language is never touched", () => {
  const body = "```python\nprint('nookboard')\n```\n\n```js\nconst a = 1;\n```\n";
  assert.deepEqual(splitQueries(body), []);
  assert.equal(spliceQueries(body, () => "SHOULD NOT APPEAR"), body);
});

test("an unclosed fence is not run", () => {
  // You are midway through typing it. Running it would swallow the rest of the
  // note into a result while you were still writing.
  const body = "Notes\n\n```nookboard\ncompleted this week\n";
  assert.deepEqual(splitQueries(body), []);
  assert.equal(spliceQueries(body, () => "SHOULD NOT APPEAR"), body);
});

test("an unclosed fence still leaves the closing tick available for showing one", () => {
  const body = "Here is one:\n\n```nookboard\ncompleted today\n```\n";
  // closed, so it runs -- closing it is what makes it live
  assert.equal(splitQueries(body).length, 1);
});

test("several blocks, each answered on its own", () => {
  const body = "```nookboard\ndays today\n```\n\nmid\n\n```nookboard\nopen tasks\n```\n";
  const out = spliceQueries(body, (q) => (q === "days today" ? "- [[2026-09-24]] Thursday" : "- [[Zebra]]"));
  assert.equal(out, "- [[2026-09-24]] Thursday\n\nmid\n\n- [[Zebra]]\n");
});

test("two identical queries are two separate blocks", () => {
  const body = "```nookboard\nopen tasks\n```\n\n```nookboard\nopen tasks\n```\n";
  assert.equal(splitQueries(body).length, 2);
});

test("a body with no queries comes back byte for byte", () => {
  const body = "Just writing.\n\n- a list\n- of things\n";
  assert.equal(spliceQueries(body, () => "NOPE"), body);
});

test("an empty answer collapses the block rather than leaving a blank line storm", () => {
  const out = spliceQueries("a\n\n```nookboard\nopen tasks\n```\n\nb\n", () => "");
  assert.equal(out, "a\n\n\n\nb\n");
});

test("the language name is matched case-insensitively", () => {
  assert.equal(splitQueries("```NookBoard\nopen tasks\n```").length, 1);
});

test("indented fences count, as they do in a nested list", () => {
  assert.equal(splitQueries("  ```nookboard\nopen tasks\n  ```").length, 1);
});

test("nonsense input does not throw", () => {
  assert.deepEqual(splitQueries(""), []);
  assert.deepEqual(splitQueries(null), []);
  assert.equal(spliceQueries(null, () => "x"), "");
  assert.equal(spliceQueries("```nookboard\n```", () => "x"), "x");
});
