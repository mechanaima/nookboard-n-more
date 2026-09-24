// reading.js — what a note says about itself.
//
// The reading dialog shows a note's properties above its body. The vocabulary lives
// in a module rather than in the view so the rule can be tested on its own, and the
// rule is: a row is drawn only when the note actually carries that fact, and the
// file's own keys come last, under their own names.
//
// `propertyRows` is pure -- it takes a note and returns labels and text -- because
// that is the part worth testing; `paintProperties` only turns those rows into the
// two elements a description list is made of.

//: The app's own fields, in the order a person reads them. `collection` and `status`
//: are always drawn, because a note always has both; everything else is skipped when
//: it is empty, since "folder:" with nothing after it is not information.
//:
//: The board column is deliberately absent: it is derived from status rather than
//: written down anywhere, and a column means something on the board, not in a note.
export const PROP_ROWS = [
  ["collection", (n) => n.collection],
  ["status",     (n) => n.status],
  ["dates",      (n) => (n.dates || []).join(", ")],
  ["time",       (n) => n.time_label],
  ["tags",       (n) => (n.tags || []).join(", ")],
  ["mood",       (n) => (n.mood == null ? "" : String(n.mood))],
  ["pain",       (n) => (n.pain == null ? "" : String(n.pain))],
  ["recurrence", (n) => n.recurrence],
  ["folder",     (n) => n.path],
  ["address",    (n) => n.url],
  ["icon",       (n) => n.icon],
  ["created",    (n) => n.created],
  ["completed",  (n) => n.completed],
];

export function propertyRows(note) {
  const rows = [];
  for (const [label, read] of PROP_ROWS) {
    let value;
    try {
      value = read(note);
    } catch {
      continue; // a field the server did not send is a field with nothing to say
    }
    if (value == null || value === "") continue;
    rows.push([label, String(value)]);
  }
  // Then whatever else the file says. These are not the app's words and are not
  // translated into them: a key is drawn under its own name, so a property nobody
  // here understands still shows up as the property it is.
  const extra = note.frontmatter_extra || {};
  for (const key of Object.keys(extra)) {
    const value = extra[key];
    if (value == null || value === "") continue;
    rows.push([key, typeof value === "object" ? JSON.stringify(value) : String(value)]);
  }
  return rows;
}

export function paintProperties(target, note) {
  target.replaceChildren();
  for (const [label, value] of propertyRows(note)) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    target.append(dt, dd);
  }
}
