// The icon helpers: naming, membership, and the picker's search.
//
// `draw()` needs a DOM and is covered by `tools/check_render.sh`; what is worth
// testing here is the *choosing* -- which names a search offers, in what order, and
// whether "this is not an icon" is answered correctly, because that answer is what
// stops a note's icon from silently not drawing.
import test from "node:test";
import assert from "node:assert/strict";
import { COUNT, LIMIT, filter, has, iconTitle, labelFor } from "./icons.js";

test("the whole set is here, and it is in Lucide's order", () => {
  assert.ok(COUNT > 1000, `${COUNT} icons looks truncated`);
  const { names } = filter("");
  assert.ok(names.length > 0);
});

test("a name reads as words", () => {
  assert.equal(labelFor("a-arrow-down"), "a arrow down");
  assert.equal(labelFor("server"), "server");
  assert.equal(labelFor(""), "");
  assert.equal(labelFor(null), "");
});

test("membership is exact: a near miss is not an icon", () => {
  assert.equal(has("server"), true);
  assert.equal(has("serverr"), false);
  assert.equal(has("Server"), false, "Lucide names are lowercase; a capital is a typo");
  assert.equal(has(""), false);
  assert.equal(has(null), false);
});

test("a search puts the name you meant first", () => {
  // `serv` should offer `server` before `cloud-server-2`: prefix beats word-start.
  const { names } = filter("server");
  assert.ok(names.includes("server"));
  assert.equal(names[0], "server");

  const serv = filter("serv").names;
  assert.ok(serv.includes("server"));
  assert.ok(serv.indexOf("server") < serv.length);
});

test("every word in the query has to match somewhere in the name", () => {
  const { names } = filter("book open");
  assert.ok(names.includes("book-open"));
  assert.equal(names.includes("book"), false, "one word of two is not a match");
});

test("the grid is bounded, and says how many there really were", () => {
  const all = filter("");
  assert.equal(all.total, COUNT);
  assert.equal(all.shown, LIMIT);
  assert.equal(all.names.length, LIMIT);

  const one = filter("server");
  assert.ok(one.total >= 1);
  assert.equal(one.shown, Math.min(one.total, LIMIT));
});

test("nothing matches is an empty answer, not the whole set", () => {
  const none = filter("zzzzznotanicon");
  assert.equal(none.total, 0);
  assert.deepEqual(none.names, []);
});

test("an icon that cannot be drawn says so rather than showing nothing", () => {
  assert.equal(iconTitle("server"), "server");
  assert.match(iconTitle("serverr"), /not a Lucide icon/);
  assert.equal(iconTitle(""), "");
  assert.equal(iconTitle(null), "");
});
