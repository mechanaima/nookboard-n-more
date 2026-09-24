// ISO week labels. The expectations below came from Python's `isocalendar`,
// which is the reference implementation -- so these assert agreement with the
// server rather than with my own arithmetic restated.

import assert from "node:assert/strict";
import test from "node:test";

import { weekKey } from "./week.js";

test("a mid-year date is in the week it looks like it is", () => {
  assert.equal(weekKey("2026-09-21"), "2026-W39"); // a Monday
  assert.equal(weekKey("2026-09-23"), "2026-W39"); // Wednesday of that week
  assert.equal(weekKey("2026-09-27"), "2026-W39"); // and its Sunday
});

test("the turn of the year belongs to the right year", () => {
  // The case that breaks naive implementations: these three days are all in
  // the final week of the *previous* ISO year.
  assert.equal(weekKey("2027-01-01"), "2026-W53");
  assert.equal(weekKey("2027-01-03"), "2026-W53");
  assert.equal(weekKey("2027-01-04"), "2027-W01");
  assert.equal(weekKey("2021-01-01"), "2020-W53");
});

test("a year can have 53 weeks", () => {
  assert.equal(weekKey("2026-12-28"), "2026-W53");
  assert.equal(weekKey("2026-12-31"), "2026-W53");
  assert.equal(weekKey("2020-12-31"), "2020-W53");
});

test("a leap day is handled like any other day", () => {
  assert.equal(weekKey("2024-02-29"), "2024-W09");
});

test("only a Monday starts a new week", () => {
  // Two years of days: the key changes on Mondays and on no other weekday.
  // This is the property, rather than another list of dates to get wrong.
  let cursor = new Date(Date.UTC(2026, 0, 1));
  const end = new Date(Date.UTC(2027, 2, 1));
  let previous = null;
  while (cursor <= end) {
    const iso = cursor.toISOString().slice(0, 10);
    const key = weekKey(iso);
    if (previous !== null && key !== previous) {
      assert.equal(cursor.getUTCDay(), 1, `${iso} started a week but is not a Monday`);
    }
    previous = key;
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
});

test("seven days from a Monday share one key, and the next does not", () => {
  const monday = new Date(Date.UTC(2026, 8, 21));
  const key = weekKey("2026-09-21");
  for (let i = 0; i < 7; i += 1) {
    const d = new Date(monday);
    d.setUTCDate(d.getUTCDate() + i);
    assert.equal(weekKey(d.toISOString().slice(0, 10)), key);
  }
  assert.notEqual(weekKey("2026-09-28"), key);
});

test("nonsense is answered with nothing, not a guess", () => {
  assert.equal(weekKey(""), null);
  assert.equal(weekKey(null), null);
  assert.equal(weekKey("2026-W39"), null);
  assert.equal(weekKey("not a date"), null);
  assert.equal(weekKey("2026-13-45"), null);
});
