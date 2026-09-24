// The dashboard's clock and card wording.

import assert from "node:assert/strict";
import test from "node:test";

import { monthGrid } from "./calendar.js";
import {
  BOARD_TILES, clockTime, greeting, longDate, statTiles, todayAction, todayLine,
  trimmedMonth,
} from "./home.js";

test("the greeting covers every hour of the day", () => {
  const at = (h) => greeting(h);
  assert.equal(at(5), "Good morning");
  assert.equal(at(11), "Good morning");
  assert.equal(at(12), "Good afternoon");
  assert.equal(at(16), "Good afternoon");
  assert.equal(at(17), "Good evening");
  assert.equal(at(21), "Good evening");
  assert.equal(at(22), "Still up");
  assert.equal(at(23), "Still up");
  // and past midnight, which is where a band table usually gets it wrong
  assert.equal(at(0), "Still up");
  assert.equal(at(4), "Still up");
});

test("every hour gets a greeting", () => {
  for (let h = 0; h < 24; h += 1) {
    assert.match(greeting(h), /\S/, `hour ${h}`);
  }
});

test("the clock pads both halves", () => {
  assert.equal(clockTime(new Date(2026, 8, 24, 9, 5)), "09:05");
  assert.equal(clockTime(new Date(2026, 8, 24, 23, 59)), "23:59");
  assert.equal(clockTime(new Date(2026, 8, 24, 0, 0)), "00:00");
});

test("the long date names the day, including the ends of the week", () => {
  // getDay() is 0 for Sunday and the list starts at Monday, which is the
  // classic off-by-one here.
  assert.equal(longDate(new Date(2026, 8, 24)), "Thursday 24 September");
  const monday = new Date(2026, 8, 21);
  const sunday = new Date(2026, 8, 27);
  assert.equal(monday.getDay(), 1);
  assert.equal(sunday.getDay(), 0);
  assert.equal(longDate(monday), "Monday 21 September");
  assert.equal(longDate(sunday), "Sunday 27 September");
});

test("stat tiles follow the reading order and skip what the server did not send", () => {
  const tiles = statTiles({ notes: 190, tasks: 44, tags: 24, collections: 5, open: 3 });
  // The vault card describes the vault, so it shows no work counts: "open" is
  // the board card's business, and it is sent but not shown here.
  assert.deepEqual(tiles.map((t) => t.key), ["notes", "tasks", "tags", "collections"]);
  assert.deepEqual(statTiles({ notes: 1 }).map((t) => t.key), ["notes"]);
  assert.deepEqual(statTiles(null), []);
});

test("a month that fits in five weeks does not reserve a sixth", () => {
  // September 2026 starts on a Tuesday and has 30 days: five rows. The sixth
  // row monthGrid always returns is all next month, so it goes. The last row
  // is short -- 32 cells is four full rows plus four -- but the cells still
  // flow into the right columns, and 32 and 35 are both five rows tall.
  const cells = trimmedMonth(monthGrid(2026, 9));
  assert.equal(Math.ceil(cells.length / 7), 5);
  assert.equal(cells[0].inMonth, false); // the leading blanks stayed
  assert.equal(cells[cells.length - 1].iso, "2026-09-30");
});

test("a month that needs six weeks keeps all six", () => {
  // August 2026 starts on a Saturday and has 31 days, so it genuinely spills
  // into a sixth row -- trimming that would drop the 30th and 31st.
  const cells = trimmedMonth(monthGrid(2026, 8));
  assert.equal(Math.ceil(cells.length / 7), 6);
  assert.equal(cells[cells.length - 1].iso, "2026-08-31");
});

test("a leap February keeps the 29th", () => {
  // Hard-coded month lengths are wrong once every four years, and a grid that
  // stops at 28 would silently lose a day of the vault's history.
  const cells = trimmedMonth(monthGrid(2028, 2));
  assert.equal(cells.filter((c) => c.inMonth).length, 29);
  assert.equal(cells[cells.length - 1].iso, "2028-02-29");
});

test("the board card gets the board's own numbers", () => {
  const tiles = statTiles({ ready: 5, open: 8, blocked: 1, done: 3, total: 11 }, BOARD_TILES);
  assert.deepEqual(tiles.map((t) => t.key), ["ready", "open", "blocked", "done"]);
  assert.deepEqual(tiles.map((t) => t.value), [5, 8, 1, 3]);
});

test("a zero is a real number and still gets a tile", () => {
  // 0 blocked is information; dropping the tile would look like the card broke.
  const keys = statTiles({ notes: 5, tasks: 0 }).map((t) => t.key);
  assert.deepEqual(keys, ["notes", "tasks"]);
});

test("the today card says how much got finished", () => {
  assert.equal(todayLine({ finished: 0 }), "Nothing finished yet");
  assert.equal(todayLine({ finished: 1 }), "1 thing finished");
  assert.equal(todayLine({ finished: 4 }), "4 things finished");
  assert.equal(todayLine(null), "Nothing finished yet");
});

test("the button promises what it will do", () => {
  assert.equal(todayAction({ has_note: false }).label, "Write today's note");
  assert.equal(todayAction({ has_note: true, summarised: false }).kind, "summarise");
  assert.equal(todayAction({ has_note: true, summarised: true }).kind, "open");
  assert.equal(todayAction(null).kind, "summarise");
});
