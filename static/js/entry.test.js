import { test } from "node:test";
import assert from "node:assert/strict";
import {
  signifierGlyph, moodEmoji, statusLabel,
  escapeHtml, highlight, heatLevel, shortDate, friendlyDate,
} from "./entry.js";

test("signifier glyphs", () => {
  assert.equal(signifierGlyph("task"), "\u2022");
  assert.equal(signifierGlyph("event"), "\u25cb");
  assert.equal(signifierGlyph("note"), "\u2013");
  assert.equal(signifierGlyph(undefined), "\u2013");
});

test("mood emoji known + unknown", () => {
  assert.equal(moodEmoji("great"), "😄");
  assert.equal(moodEmoji("bad"), "😢");
  assert.equal(moodEmoji("nonsense"), "");
  assert.equal(moodEmoji(null), "");
});

test("status labels", () => {
  assert.equal(statusLabel("complete"), "done");
  assert.equal(statusLabel("irrelevant"), "dropped");
  assert.equal(statusLabel("open"), "open");
});

test("escapeHtml neutralises markup", () => {
  assert.equal(escapeHtml('<script>alert("x")</script>'),
    "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;");
});

test("highlight wraps matches", () => {
  assert.equal(highlight("Buy oat milk", "milk"), "Buy oat <mark>milk</mark>");
});

test("highlight is case-insensitive", () => {
  assert.equal(highlight("Milk and milk", "MILK"),
    "<mark>Milk</mark> and <mark>milk</mark>");
});

test("highlight escapes HTML in the source", () => {
  const out = highlight("<img onerror=x>", "img");
  assert.doesNotMatch(out, /<img/);
  assert.match(out, /&lt;<mark>img<\/mark>/);
});

test("highlight treats regex metachars literally", () => {
  assert.equal(highlight("cost is $5 (approx)", "("), "cost is $5 <mark>(</mark>approx)");
});

test("highlight with empty query just escapes", () => {
  assert.equal(highlight("<b>hi</b>", ""), "&lt;b&gt;hi&lt;/b&gt;");
});

test("heat levels", () => {
  assert.equal(heatLevel(0), 0);
  assert.equal(heatLevel(1), 1);
  assert.equal(heatLevel(2), 2);
  assert.equal(heatLevel(3), 2);
  assert.equal(heatLevel(4), 3);
  assert.equal(heatLevel(99), 3);
});

test("shortDate formats", () => {
  assert.equal(shortDate("2026-09-23"), "Sep 23");
  assert.equal(shortDate("2026-01-05"), "Jan 5");
  assert.equal(shortDate(""), "");
});

test("friendlyDate says today", () => {
  assert.equal(friendlyDate("2026-09-23", "2026-09-23"), "today");
  assert.equal(friendlyDate("2026-09-22", "2026-09-23"), "Sep 22");
});
