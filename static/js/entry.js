// Pure display helpers for entry rendering. No DOM, fully testable.
export function signifierGlyph(signifier) {
  if (signifier === "task") return "\u2022";   // •
  if (signifier === "event") return "\u25cb";  // ○
  return "\u2013";                             // –
}

export function moodEmoji(mood) {
  return ({
    great: "\u{1F604}",  // 😄
    good:  "\u{1F642}",  // 🙂
    meh:   "\u{1F610}",  // 😐
    low:   "\u{1F615}",  // 😕
    bad:   "\u{1F622}",  // 😢
  })[mood] || "";
}

export function statusLabel(status) {
  return ({
    open: "open",
    complete: "done",
    migrated: "migrated",
    scheduled: "scheduled",
    irrelevant: "dropped",
  })[status] || status || "";
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Format a Date as YYYY-MM-DD using LOCAL calendar fields.
// NEVER use toISOString() for this: it converts to UTC first, so a local
// midnight lands on the previous day in any timezone east of Greenwich,
// and "now" lands on tomorrow for western timezones late in the day.
export function localIsoDate(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// Wrap query matches in <mark>, escaping everything else exactly once.
export function highlight(text, query) {
  const src = String(text ?? "");
  if (!query || !query.trim()) return escapeHtml(src);
  const q = query.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(q, "gi");
  let out = "";
  let last = 0;
  for (const m of src.matchAll(re)) {
    out += escapeHtml(src.slice(last, m.index));
    out += `<mark>${escapeHtml(m[0])}</mark>`;
    last = m.index + m[0].length;
  }
  return out + escapeHtml(src.slice(last));
}

// 0 = none, 1..3 = increasing heat
export function heatLevel(count) {
  if (!count || count <= 0) return 0;
  if (count === 1) return 1;
  if (count <= 3) return 2;
  return 3;
}

// "2026-09-23" -> "Sep 23"
export function shortDate(iso) {
  if (!iso || iso.length < 10) return iso || "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  const names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const idx = Number(m) - 1;
  if (idx < 0 || idx > 11) return iso;
  return `${names[idx]} ${Number(d)}`;
}

// "2026-09-23" -> "2026-09-23" label for today/yesterday, else shortDate
export function friendlyDate(iso, todayIso) {
  if (!iso) return "";
  if (todayIso && iso.slice(0, 10) === todayIso) return "today";
  return shortDate(iso);
}
