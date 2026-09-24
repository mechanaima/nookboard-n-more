// What a time means, in the words the editor needs.
//
// The server owns the *spelling* of a time (`time_label`: `09:05`, or
// `14:30–16:00`), the same way it owns `dates`, so nothing here reassembles a
// range or pads an hour — `9:05` in a file is `09:05` everywhere the client
// sees it because one layer decided that, not four.
//
// What is left for the client is the rule a person cannot guess: a time with no
// date names no instant, so it will not fire and no calendar will show it. That
// is the entire reason this module exists, and it is why `timeHint` takes the
// count of days rather than assuming one.

export function hasTime(note) {
  return Boolean(note && note.time_label);
}

export function timeHint(at, dayCount) {
  if (!at) return "";
  if (!dayCount) return "no date — a time without one will not fire";
  if (dayCount === 1) return `fires at ${at}`;
  return `fires at ${at}, on each of those ${dayCount} days`;
}
