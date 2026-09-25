// The dashboard's clock, and the small pieces its cards are painted from.
//
// Everything about the vault comes from /api/home: the server owns those
// numbers so the cards cannot disagree with the views they stand for. This
// module holds only what the browser knows better -- the time where you
// actually are, and the wording of the cards.

const WEEKDAYS = [
  "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

//: When the greeting changes, as [first hour, words]. Late night is its own
//: band rather than "good night" at 2am, which reads like a dismissal.
const GREETINGS = [
  [5, "Good morning"],
  [12, "Good afternoon"],
  [17, "Good evening"],
  [22, "Still up"],
];

export function greeting(hour) {
  let words = GREETINGS[GREETINGS.length - 1][1];
  for (const [from, text] of GREETINGS) {
    if (hour >= from) words = text;
  }
  return words;
}

export function clockTime(now) {
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

// "Thursday 24 September" -- the day named, because a dashboard is read at a
// glance and a numeric date makes you do the work.
export function longDate(now) {
  return `${WEEKDAYS[(now.getDay() + 6) % 7]} ${now.getDate()} ${MONTHS[now.getMonth()]}`;
}

// monthGrid() always returns six weeks, so a month that fits in five carries a
// whole empty row at the bottom. The leading blanks have to stay -- they are
// what pushes the 1st under its weekday -- but the trailing ones are next
// month and can go. The last in-month cell is the last day of the month, so
// this is also where a leap February proves itself.
export function trimmedMonth(cells) {
  let end = cells.length;
  while (end > 0 && !cells[end - 1].inMonth) end -= 1;
  return cells.slice(0, end);
}

// The tiles of the statistics card, in reading order.
//: rather than in the markup so adding a number is one line -- and so the card
//: cannot show a tile the server does not send.
const TILES = [
  ["notes", "Notes"],
  ["tasks", "Tasks"],
  ["tags", "Tags"],
  ["collections", "Collections"],
];

//: The board card's numbers, in the order it reads them.
export const BOARD_TILES = [
  ["ready", "Ready"],
  ["open", "Open"],
  ["blocked", "Blocked"],
  ["done", "Done"],
  ["due_soon", "Due Soon", "peach"],
];

// A key the server did not send is skipped rather than shown as a dash: the
// tiles are meant to be the numbers, and a placeholder would look like a zero.
// A zero it did send is kept, because "0 blocked" is information.
export function statTiles(stats, spec = TILES) {
  return spec
    .filter(([key]) => stats && stats[key] !== undefined)
    .map((tile) => ({
      key: tile[0],
      label: tile[1],
      value: stats[tile[0]],
      color: tile[2] || null,
    }));
}

export function todayLine(card) {
  const n = card && card.finished ? card.finished : 0;
  if (n === 0) return "Nothing finished yet";
  if (n === 1) return "1 thing finished";
  return `${n} things finished`;
}

//: The button says what it will actually do. `write` and `create` are the same
//: request (the day's note is written by the run that summarises it), but they
//: are not the same promise, and the label should be the true one.
export function todayAction(card) {
  if (!card || !card.has_note) return { label: "Write today's note", kind: "summarise" };
  if (!card.summarised) return { label: "Write the recap", kind: "summarise" };
  return { label: "Open today's note", kind: "open" };
}
