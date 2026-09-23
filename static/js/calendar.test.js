import { test } from "node:test";
import assert from "node:assert/strict";
import { monthGrid, shiftMonth } from "./calendar.js";

test("January 2024 starts on a Monday — first cell is 2023-12-31", () => {
  const cells = monthGrid(2024, 1);
  assert.equal(cells[0].iso, "2023-12-31");
});

test("January 2024 grid fills 42 cells", () => {
  const cells = monthGrid(2024, 1);
  assert.equal(cells.length, 42);
});

test("last cell of January 2024 is 2024-02-10", () => {
  const cells = monthGrid(2024, 1);
  assert.equal(cells[41].iso, "2024-02-10");
});

test("inMonth only true for Jan cells", () => {
  const cells = monthGrid(2024, 1);
  const inMonth = cells.filter((c) => c.inMonth);
  assert.equal(inMonth.length, 31); // Jan has 31 days
  for (const c of cells) {
    if (c.iso.startsWith("2024-01")) {
      assert.ok(c.inMonth, `${c.iso} should be inMonth`);
    } else {
      assert.ok(!c.inMonth, `${c.iso} should NOT be inMonth`);
    }
  }
});

test("February 2024 leap year still 42 cells", () => {
  const cells = monthGrid(2024, 2);
  assert.equal(cells.length, 42);
  const inMonth = cells.filter((c) => c.inMonth);
  assert.equal(inMonth.length, 29); // 2024 is leap year
});

test("shiftMonth forward", () => {
  assert.deepEqual(shiftMonth(2026, 9, 1), [2026, 10]);
  assert.deepEqual(shiftMonth(2026, 12, 1), [2027, 1]);
});

test("shiftMonth backward", () => {
  assert.deepEqual(shiftMonth(2026, 1, -1), [2025, 12]);
  assert.deepEqual(shiftMonth(2026, 9, -1), [2026, 8]);
});