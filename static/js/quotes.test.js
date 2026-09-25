// node --test — the dashboard's daily quote.
//
// Three promises are worth pinning here, because the whole point of a *daily*
// quote is that it is not a quote per page load:
//   1. the same day always shows the same quote (no Math.random anywhere),
//   2. the next day shows a different one,
//   3. every quote in the list is reachable -- a quote that can never come up is
//      dead weight in a file this long.

import assert from "node:assert/strict";
import test from "node:test";

import { QUOTES, attribution, dayKey, quoteForDay, quoteOfTheDay } from "./quotes.js";

//: The walk is done in local dates on purpose: the picker reads the browser's
//: calendar, and a test that used UTC dates would be testing another function.
function at(y, m, d, h = 12, min = 0) {
  return new Date(y, m - 1, d, h, min);
}

test("the list is fifteen figures with six quotes each", () => {
  assert.equal(QUOTES.length, 90);
  assert.equal(new Set(QUOTES.map((q) => q.name)).size, 15);
});

test("every quote has text, a figure and a source", () => {
  for (const quote of QUOTES) {
    assert.match(quote.text, /\S/, `a text for ${quote.name}`);
    assert.match(quote.name, /\S/, "a figure to attribute it to");
    assert.match(quote.role, /\S/, `a role for ${quote.name}`);
    assert.match(quote.source, /\S/, `a source for "${quote.text.slice(0, 24)}…"`);
  }
});

test("the day key is the local date, zero padded", () => {
  assert.equal(dayKey(at(2026, 9, 25)), "2026-09-25");
  assert.equal(dayKey(at(2026, 1, 5)), "2026-01-05");
  assert.equal(dayKey(at(2026, 12, 31, 23, 59)), "2026-12-31");
  // Midnight is the *next* day, which is where a quote rotates.
  assert.equal(dayKey(at(2027, 1, 1, 0, 0)), "2027-01-01");
});

test("the same day shows the same quote, whenever it is asked", () => {
  for (const hour of [0, 6, 12, 18, 23]) {
    const quote = quoteOfTheDay(at(2026, 9, 25, hour, 30));
    assert.equal(quote, quoteOfTheDay(at(2026, 9, 25, hour, 59)),
      `hour ${hour} should not change the quote`);
  }
  // And it is a pure function of the day: no clock, no random, no state.
  const day = "2026-09-25";
  assert.equal(quoteForDay(day), quoteForDay(day));
  assert.equal(quoteForDay(day), quoteForDay(day, QUOTES));
});

test("the quote changes with the day", () => {
  const start = at(2026, 9, 25);
  let distinct = new Set();
  for (let i = 0; i < 90; i += 1) {
    const day = new Date(start);
    day.setDate(day.getDate() + i);
    distinct.add(quoteOfTheDay(day).text);
  }
  // Ninety days and ninety quotes: the hash should not be so lumpy that a
  // quarter of them never come up in three months.
  assert.ok(distinct.size >= 60,
    `only ${distinct.size} of 90 quotes appeared in 90 days`);
});

test("the day before is a real calendar day, not arithmetic on the string", () => {
  // The lookback walks through a month end and a leap February. If `dayBefore`
  // built the previous day by subtracting from the text, the guard would be
  // comparing today's pick against a day that does not exist -- so this walks
  // the boundaries and asks only that every day still has a quote.
  const walk = [at(2026, 2, 27), at(2026, 3, 1), at(2026, 12, 31), at(2028, 2, 29),
    at(2028, 3, 1)];
  for (const day of walk) {
    const quote = quoteOfTheDay(day);
    assert.ok(QUOTES.includes(quote), `${dayKey(day)} should still show a quote`);
  }
  assert.equal(quoteForDay("2026-03-01"), quoteForDay("2026-03-01"));
  // A key that is not a date is not a crash: the picker still answers.
  assert.ok(QUOTES.includes(quoteForDay("not-a-date")));
});

test("every quote comes up eventually, and no figure is passed over for a year", () => {
  const seen = new Set();
  const figures = new Set();
  const start = at(2026, 1, 1);
  let repeats = 0;
  for (let i = 0; i < 365; i += 1) {
    const day = new Date(start);
    day.setDate(day.getDate() + i);
    const quote = quoteOfTheDay(day);
    seen.add(quote.text);
    figures.add(quote.name);
    const yesterday = new Date(day);
    yesterday.setDate(yesterday.getDate() - 1);
    if (quoteOfTheDay(yesterday).text === quote.text) repeats += 1;
  }
  assert.equal(figures.size, 15, "a figure should not wait more than a year to be heard from");
  assert.ok(seen.size >= 85, `only ${seen.size} of 90 quotes appeared in a year`);

  // Twenty years, the whole list, and the one repeat the picker does not chase:
  // it looks back a single day, so three days running can land on one quote.
  // That is about one day in eight thousand, and the count stays in single
  // digits -- if a change to the picker makes repeats common, this is the test
  // that says so.
  const long = new Set();
  let longRepeats = 0;
  for (let i = 0; i < 365 * 20; i += 1) {
    const day = new Date(start);
    day.setDate(day.getDate() + i);
    long.add(quoteOfTheDay(day).text);
    const yesterday = new Date(day);
    yesterday.setDate(yesterday.getDate() - 1);
    if (quoteOfTheDay(yesterday).text === quoteOfTheDay(day).text) longRepeats += 1;
  }
  assert.equal(long.size, QUOTES.length, "every quote in the list should be reachable");
  assert.ok(longRepeats <= 12,
    `${longRepeats} days in twenty years repeated the previous day's quote`);
  assert.ok(repeats <= 2, `${repeats} days in 2026 repeated the previous day's quote`);
});

test("a quote is attributed to its figure and its source", () => {
  const quote = QUOTES.find((q) => q.name === "Ursula K. Le Guin");
  assert.equal(attribution(quote), "— Ursula K. Le Guin, A Wizard of Earthsea (1968)");
  assert.equal(attribution({ ...quote, source: "" }), "— Ursula K. Le Guin");
  assert.equal(attribution(null), "");
});

test("a quote of the day without an argument is still a quote", () => {
  const quote = quoteOfTheDay();
  assert.ok(QUOTES.includes(quote));
});
