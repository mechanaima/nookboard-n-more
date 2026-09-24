// Tests for the mood view helpers.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  WEEK_START, buildGrid, dayLabel, distribution, findCheckin, fromIso,
  painText, recentDays, statChips,
} from "./mood.js";
import { localIsoDate } from "./entry.js";

const d = (date, mood = null, pain = null, count = 1) => ({ date, mood, pain, count });

// ---------------------------------------------------------------------------
// fromIso — the timezone trap
// ---------------------------------------------------------------------------

test("fromIso parses in local time, not UTC", () => {
  const got = fromIso("2026-09-01");
  assert.equal(got.getFullYear(), 2026);
  assert.equal(got.getMonth(), 8);
  assert.equal(got.getDate(), 1);
});

test("fromIso round-trips through localIsoDate", () => {
  // `new Date("2026-09-01")` is UTC midnight and would fail this west of
  // Greenwich; the whole point is that these two agree.
  for (const iso of ["2026-01-01", "2026-06-15", "2026-12-31"]) {
    assert.equal(localIsoDate(fromIso(iso)), iso);
  }
});

// ---------------------------------------------------------------------------
// buildGrid
// ---------------------------------------------------------------------------

test("grid weeks always hold exactly seven cells", () => {
  const weeks = buildGrid([], "2026-09-01", "2026-09-30");
  assert.ok(weeks.length > 0);
  for (const week of weeks) assert.equal(week.cells.length, 7);
});

test("grid pads to whole weeks so a weekday keeps its column", () => {
  // 2026-09-01 is a Tuesday, so a Monday-start grid needs one lead cell.
  // The range is Tue..Sun: exactly one Monday-start week.
  const weeks = buildGrid([], "2026-09-01", "2026-09-06");
  assert.equal(weeks.length, 1);
  assert.equal(weeks[0].cells[0].date, "2026-08-31"); // Monday before
  assert.equal(weeks[0].cells[0].inRange, false);
  assert.equal(weeks[0].cells[1].date, "2026-09-01");
  assert.equal(weeks[0].cells[1].inRange, true);
});

test("a range ending on the first day of a week needs a partial second week", () => {
  // Sep 1..Sep 7 2026 is eight days once padded, so it cannot fit in one row.
  const weeks = buildGrid([], "2026-09-01", "2026-09-07");
  assert.equal(weeks.length, 2);
  assert.equal(weeks[1].cells.filter((c) => c.inRange).length, 1);
});

test("grid marks trailing days after the range", () => {
  const weeks = buildGrid([], "2026-09-01", "2026-09-02");
  const flat = weeks.flatMap((w) => w.cells);
  assert.equal(flat.filter((c) => c.inRange).length, 2);
  assert.ok(flat.some((c) => !c.inRange));
});

test("out-of-range cells never carry a reading", () => {
  // A note dated outside the window must not paint a cell that the range
  // excluded, or the view would show data the API was asked not to return.
  const weeks = buildGrid([d("2026-08-31", "bad")], "2026-09-01", "2026-09-07");
  assert.equal(weeks[0].cells[0].day, null);
});

test("grid attaches readings to their own day", () => {
  const weeks = buildGrid([d("2026-09-03", "good", 4)], "2026-09-01", "2026-09-07");
  const cell = weeks.flatMap((w) => w.cells).find((c) => c.date === "2026-09-03");
  assert.equal(cell.day.mood, "good");
  assert.equal(cell.day.pain, 4);
});

test("an unlogged in-range day is null, not missing", () => {
  const weeks = buildGrid([], "2026-09-01", "2026-09-03");
  const cell = weeks.flatMap((w) => w.cells).find((c) => c.date === "2026-09-02");
  assert.equal(cell.day, null);
  assert.equal(cell.inRange, true);
});

test("a week is only labelled when the month changes", () => {
  // Otherwise the axis repeats "Sep" once per column.
  const weeks = buildGrid([], "2026-09-01", "2026-11-30");
  const labels = weeks.filter((w) => w.month).map((w) => w.month);
  assert.deepEqual(labels, ["Aug", "Sep", "Oct", "Nov"]);
});

test("the first week is labelled even if it starts mid-month", () => {
  const weeks = buildGrid([], "2026-09-15", "2026-09-20");
  assert.equal(weeks[0].month, "Sep");
});

test("a custom week start shifts the leading pad", () => {
  // Sunday-start: 2026-09-01 (Tue) needs two lead cells.
  const weeks = buildGrid([], "2026-09-01", "2026-09-07", 0);
  assert.equal(weeks[0].cells[0].date, "2026-08-30");
  assert.equal(WEEK_START, 1, "default stays ISO Monday");
});

// ---------------------------------------------------------------------------
// findCheckin
// ---------------------------------------------------------------------------

test("findCheckin picks the tagged note and nothing else", () => {
  const notes = [{ id: "a", tags: ["work"] }, { id: "b", tags: ["mood"] }];
  assert.equal(findCheckin(notes).id, "b");
});

test("findCheckin returns null rather than guessing at an untagged note", () => {
  // Logging must not attach itself to a note the user wrote.
  assert.equal(findCheckin([{ id: "a", tags: [] }, { id: "b" }]), null);
  assert.equal(findCheckin([]), null);
  assert.equal(findCheckin(undefined), null);
});

// ---------------------------------------------------------------------------
// Small formatters
// ---------------------------------------------------------------------------

test("painText renders a dash when nothing was logged", () => {
  assert.equal(painText(null), "\u2013");
  assert.equal(painText(undefined), "\u2013");
  assert.equal(painText(0), "0/10");
  assert.equal(painText(7), "7/10");
});

test("statChips is empty before anything is logged", () => {
  assert.deepEqual(statChips(null), []);
  assert.deepEqual(statChips({ days_logged: 0, streak: 0, entries: 0 }), []);
});

test("statChips omits a zero streak and an absent pain average", () => {
  const chips = statChips({
    days_logged: 3, streak: 0, avg_mood: "good", avg_pain: null, entries: 3,
  });
  const labels = chips.map((c) => c.label);
  assert.deepEqual(labels, ["days logged", "avg mood"]);
});

test("statChips shows the entry count only when it exceeds the day count", () => {
  const one = statChips({ days_logged: 2, streak: 1, avg_mood: "meh", avg_pain: 3, entries: 2 });
  assert.ok(!one.some((c) => c.label === "entries"));
  const many = statChips({ days_logged: 2, streak: 1, avg_mood: "meh", avg_pain: 3, entries: 5 });
  assert.ok(many.some((c) => c.label === "entries" && c.value === "5"));
});

test("statChips formats pain out of ten", () => {
  const chips = statChips({ days_logged: 1, streak: 1, avg_mood: "low", avg_pain: 3.5, entries: 1 });
  assert.equal(chips.find((c) => c.label === "avg pain").value, "3.5/10");
});

// ---------------------------------------------------------------------------
// distribution / recentDays / dayLabel
// ---------------------------------------------------------------------------

test("distribution keeps the level order the API gave", () => {
  const levels = ["great", "good", "meh", "low", "bad"];
  const got = distribution({ good: 3, bad: 1 }, levels, 4);
  assert.deepEqual(got.map((r) => r.level), levels);
  assert.equal(got.find((r) => r.level === "good").pct, 75);
  assert.equal(got.find((r) => r.level === "bad").pct, 25);
  assert.equal(got.find((r) => r.level === "low").count, 0);
});

test("distribution divides by nothing gracefully", () => {
  const got = distribution({}, ["good"], 0);
  assert.deepEqual(got, [{ level: "good", count: 0, pct: 0 }]);
});

test("recentDays returns newest first and caps the length", () => {
  const days = [d("2026-09-01"), d("2026-09-02"), d("2026-09-03")];
  assert.deepEqual(recentDays(days, 2).map((x) => x.date), ["2026-09-03", "2026-09-02"]);
  assert.deepEqual(recentDays(days, 10).length, 3);
  assert.deepEqual(recentDays(undefined), []);
});

test("dayLabel names what a cell holds", () => {
  assert.equal(dayLabel(null), "");
  assert.equal(dayLabel(d("2026-09-01", "good")), "good");
  assert.equal(dayLabel(d("2026-09-01", "low", 6)), "low \u00b7 pain 6/10");
  assert.equal(dayLabel(d("2026-09-01", "bad", 8, 3)), "bad \u00b7 pain 8/10 \u00b7 3 entries");
  assert.equal(dayLabel(d("2026-09-01", null, 2)), "pain 2/10");
});
