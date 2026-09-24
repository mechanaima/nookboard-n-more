// Pure helpers for the mood view. No DOM, fully testable.
//
// The mood vocabulary (level names and their order) is NOT duplicated here: the
// API serves `levels`, and every helper that needs the order takes it as an
// argument. Adding a level should be a change in one place, not two.
import { localIsoDate } from "./entry.js";

const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

//: Weeks start on Monday (ISO 8601). A habit-style grid is read as a working
//: week, and it makes the weekend a contiguous block at the end.
export const WEEK_START = 1;

// Parse "YYYY-MM-DD" into a LOCAL Date.
// Never `new Date(iso)`: that form is parsed as UTC midnight, so anywhere west
// of Greenwich every date lands a day early. That is the same trap
// localIsoDate() exists to avoid, running in the other direction.
export function fromIso(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

// Lay a series out as calendar weeks.
//
// Returns an array of weeks, each `{ month, cells }`, where `cells` is exactly
// seven entries of `{ date, inRange, day }`. Days outside the requested range
// are marked rather than dropped, because the grid needs their slots to keep
// every weekday in its own column.
//
// `month` is the label to draw above that column, or null when the month is
// unchanged from the previous week — which is what stops the axis repeating
// "Sep" eleven times.
export function buildGrid(days, from, to, weekStart = WEEK_START) {
  const byDate = new Map((days || []).map((d) => [d.date, d]));
  const start = fromIso(from);
  const end = fromIso(to);
  const lead = ((start.getDay() - weekStart) + 7) % 7;
  const cursor = new Date(start);
  cursor.setDate(cursor.getDate() - lead);

  const weeks = [];
  let lastMonth = null;
  while (cursor <= end) {
    const cells = [];
    for (let i = 0; i < 7; i += 1) {
      const iso = localIsoDate(cursor);
      const inRange = cursor >= start && cursor <= end;
      cells.push({
        date: iso,
        inRange,
        day: inRange ? byDate.get(iso) || null : null,
      });
      cursor.setDate(cursor.getDate() + 1);
    }
    const month = Number(cells[0].date.slice(5, 7)) - 1;
    weeks.push({ month: month === lastMonth ? null : MONTHS[month], cells });
    lastMonth = month;
  }
  return weeks;
}

// Which of today's notes a mood reading should be written to.
//
// Deliberately narrow: only a note carrying the tag. Logging a mood must not
// silently attach itself to whatever note happens to be dated today — that
// would rewrite a journal entry the user wrote themselves.
export function findCheckin(notes, tag = "mood") {
  return (notes || []).find((n) => (n.tags || []).includes(tag)) || null;
}

export function painText(pain) {
  return pain === null || pain === undefined ? "\u2013" : `${pain}/10`;
}

// Headline numbers as label/value pairs, so the view stays markup and the
// wording stays testable. Empty when nothing has been logged, which is what
// lets the view show one honest "nothing yet" line instead of a row of zeros.
export function statChips(summary) {
  if (!summary || !summary.days_logged) return [];
  const out = [{ label: "days logged", value: String(summary.days_logged) }];
  if (summary.streak) out.push({ label: "day streak", value: String(summary.streak) });
  if (summary.avg_mood) out.push({ label: "avg mood", value: summary.avg_mood });
  if (summary.avg_pain !== null && summary.avg_pain !== undefined) {
    out.push({ label: "avg pain", value: `${summary.avg_pain}/10` });
  }
  if (summary.entries > summary.days_logged) {
    out.push({ label: "entries", value: String(summary.entries) });
  }
  return out;
}

export function distribution(counts, levels, total) {
  return (levels || []).map((level) => {
    const count = (counts && counts[level]) || 0;
    return { level, count, pct: total ? Math.round((count / total) * 100) : 0 };
  });
}

// Newest first, for a "recent days" list — the series arrives oldest first
// because that is the order a chart wants.
export function recentDays(days, n = 14) {
  return (days || []).slice(-n).reverse();
}

// A one-line description of a day, for a title attribute.
export function dayLabel(day) {
  if (!day) return "";
  const bits = [];
  if (day.mood) bits.push(day.mood);
  if (day.pain !== null && day.pain !== undefined) bits.push(`pain ${day.pain}/10`);
  if (day.count > 1) bits.push(`${day.count} entries`);
  return bits.join(" \u00b7 ");
}
