import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRapidInput } from "./rapid.js";

test("parses task bullet", () => {
  const r = parseRapidInput("• Buy oat milk");
  assert.equal(r.signifier, "task");
  assert.equal(r.body, "Buy oat milk");
  assert.equal(r.title, "Buy oat milk");
});

test("parses event circle", () => {
  const r = parseRapidInput("○ Lunch with sarah");
  assert.equal(r.signifier, "event");
});

test("parses note dash", () => {
  const r = parseRapidInput("– readme said so");
  assert.equal(r.signifier, "note");
});

test("defaults when no signifier", () => {
  const r = parseRapidInput("hello world");
  assert.equal(r.signifier, "note");
});

test("empty returns null", () => {
  assert.equal(parseRapidInput("   "), null);
});