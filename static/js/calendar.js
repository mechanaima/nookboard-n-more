// Pure helpers — testable without DOM.
import { localIsoDate } from "./entry.js";

export function monthGrid(year, month /* 1-12 */) {
  // Return 42 cells (6 weeks × 7 days) starting Sunday.
  const first = new Date(year, month - 1, 1);
  const startOffset = first.getDay(); // 0=Sun
  const start = new Date(year, month - 1, 1 - startOffset);
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    cells.push({
      // localIsoDate, not toISOString — see the note on that helper
      iso: localIsoDate(d),
      day: d.getDate(),
      inMonth: d.getMonth() === month - 1,
    });
  }
  return cells;
}

export function shiftMonth(year, month, delta) {
  // Returns [newYear, newMonth]
  const total = year * 12 + (month - 1) + delta;
  return [Math.floor(total / 12), (total % 12) + 1];
}