// The words the Bookmarks view shows. The facts are the server's; what is worth
// testing here is only the counting shown to a person.
import test from "node:test";
import assert from "node:assert/strict";
import { countLine, emptyHint } from "./bookmarks.js";

test("the line says how many, and counts one bookmark as one bookmark", () => {
  assert.equal(countLine({ count: 1, unusable: 0 }), "1 bookmark");
  assert.equal(countLine({ count: 4, unusable: 0 }), "4 bookmarks");
});

test("an empty view says so rather than showing nothing", () => {
  assert.equal(countLine({ count: 0, unusable: 0 }), "Nothing bookmarked yet.");
});

test("the ones that cannot be opened are part of the same sentence", () => {
  assert.equal(countLine({ count: 2, unusable: 1 }), "1 bookmark \u00b7 1 that cannot be opened");
  assert.equal(countLine({ count: 3, unusable: 2 }), "1 bookmark \u00b7 2 that cannot be opened");
});

test("an address that cannot be opened is not counted as a bookmark that works", () => {
  // count includes it, so the working number is the subtraction -- and getting this
  // wrong is how a view says "1 bookmark" above a list of nothing usable.
  assert.equal(countLine({ count: 1, unusable: 1 }), "0 bookmarks \u00b7 1 that cannot be opened");
});

test("the empty state says what a bookmark is", () => {
  const hint = emptyHint();
  assert.match(hint, /url:/);
  assert.match(hint, /tag/);
});
