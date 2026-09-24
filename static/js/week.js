// ISO week labels, for addressing a weekly review.
//
// Its own module because this is the kind of date arithmetic that is right
// almost always: the awkward cases are the turn of the year, where 1 January
// can belong to the previous year's final week and a year can have 53 weeks.
// Separated from the UI, those cases can be tested directly against dates
// instead of only through a button.

function parseIso(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!m) return null;
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const d = new Date(Date.UTC(year, month - 1, day));
  if (Number.isNaN(d.getTime())) return null;
  // Date.UTC rolls overflow forward -- month 13 becomes January, day 45 becomes
  // the middle of the next month -- so a nonsense date would come back as a real
  // one and get a confident week label. Check it round-trips instead. The server
  // already refuses these (date.fromisoformat), and the client agreeing with it
  // matters more than being forgiving.
  const same =
    d.getUTCFullYear() === year &&
    d.getUTCMonth() === month - 1 &&
    d.getUTCDate() === day;
  return same ? d : null;
}

function mondayOf(d) {
  const out = new Date(d);
  out.setUTCDate(out.getUTCDate() - ((out.getUTCDay() + 6) % 7));
  return out;
}

function thursdayOfWeekOne(year) {
  // 4 January is always in week 1, so the Thursday of its week anchors the year.
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const thursday = mondayOf(jan4);
  thursday.setUTCDate(thursday.getUTCDate() + 3);
  return thursday;
}

export function weekKey(iso) {
  const day = parseIso(iso);
  if (!day) return null;
  // The week belongs to whichever year its Thursday falls in -- that single
  // rule is the whole of ISO 8601 week numbering.
  const thursday = mondayOf(day);
  thursday.setUTCDate(thursday.getUTCDate() + 3);
  const year = thursday.getUTCFullYear();
  const first = thursdayOfWeekOne(year);
  const week = 1 + Math.round((thursday - first) / (7 * 24 * 60 * 60 * 1000));
  return `${year}-W${String(week).padStart(2, "0")}`;
}
