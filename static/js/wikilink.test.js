import { test } from "node:test";
import assert from "node:assert/strict";
import { extractWikilinks, renderWikilinks } from "./wikilink.js";

test("extracts single link", () => {
  assert.deepEqual(extractWikilinks("See [[Garden tour]] today"), ["Garden tour"]);
});

test("extracts multiple, including duplicates", () => {
  const links = extractWikilinks("Link to [[One]] and [[Two]] and [[One]] again");
  assert.deepEqual(links, ["One", "Two", "One"]);
});

test("empty body → no links", () => {
  assert.deepEqual(extractWikilinks("nothing here"), []);
});

test("renders existing wikilink", () => {
  const html = renderWikilinks("See [[Garden tour]] today", []);
  assert.match(html, /<a class="wikilink missing" data-title="Garden tour">Garden tour<\/a>/);
});

test("renders missing vs existing", () => {
  const html = renderWikilinks("[[Garden tour]] and [[Other]]", ["Garden tour"]);
  assert.match(html, /class="wikilink exists"/);
  assert.match(html, /class="wikilink missing"/);
});

test("escapes HTML in title", () => {
  const html = renderWikilinks("[[<script>x</script>]]", []);
  assert.doesNotMatch(html, /<script>/);
});