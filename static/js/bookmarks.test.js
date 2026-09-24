// The words the Bookmarks view shows. The facts are the server's; what is worth
// testing here is only the counting and the wording shown to a person.
import test from "node:test";
import assert from "node:assert/strict";
import { countLine, emptyHint, statusTitle, statusWord } from "./bookmarks.js";

test("the line says how many, and counts one bookmark as one bookmark", () => {
  assert.equal(countLine({ count: 1, unusable: 0 }), "1 bookmark");
  assert.equal(countLine({ count: 4, unusable: 0 }), "4 bookmarks");
});

test("an empty view says so rather than showing nothing", () => {
  assert.equal(countLine({ count: 0, unusable: 0 }), "Nothing bookmarked yet.");
});

test("the ones that cannot be opened are part of the same sentence", () => {
  assert.equal(countLine({ count: 2, unusable: 1 }), "1 bookmark \u00b7 1 that cannot be opened");
  assert.equal(
    countLine({ count: 1, unusable: 1 }),
    "0 bookmarks \u00b7 1 that cannot be opened",
  );
});

test("a check adds when it happened, and only when it happened", () => {
  const before = countLine({ count: 2, unusable: 0 }, null);
  assert.equal(before, "2 bookmarks");
  assert.equal(
    countLine({ count: 2, unusable: 0 }, { checked_words: "just now" }),
    "2 bookmarks \u00b7 checked just now",
  );
  // a check object that carries no age must not produce "checked undefined"
  assert.equal(countLine({ count: 2, unusable: 0 }, {}), "2 bookmarks");
});

test("a chip with no result says not checked rather than nothing", () => {
  assert.equal(statusWord(null), "not checked");
  assert.equal(statusWord({ words: "answering" }), "answering");
  assert.equal(statusWord({}), "not checked");
});

test("the tooltip carries the detail, and never invents a duration", () => {
  assert.equal(statusTitle(null), "not checked yet");
  assert.equal(
    statusTitle({ words: "answering", ms: 4, at: "2026-09-24T22:00:00+00:00" }),
    "answering \u2014 4ms \u2014 at 2026-09-24T22:00:00+00:00",
  );
  // A check that never completed has no ms: it must not claim "undefinedms".
  assert.equal(statusTitle({ words: "not checked" }), "not checked");
  assert.match(
    statusTitle({ words: "no answer", why: "nothing is listening on that port", ms: 3 }),
    /nothing is listening on that port/,
  );
});

test("the empty state says what a bookmark is", () => {
  const hint = emptyHint();
  assert.match(hint, /url:/);
  assert.match(hint, /tag/);
});
