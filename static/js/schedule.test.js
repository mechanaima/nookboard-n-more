// The wording for a time on the form.
//
// Only one thing here is load-bearing, and it is the rule a person cannot guess
// by looking at the field: a time with no date names no instant, so nothing
// will fire and no calendar will show it. It has to be said, and it has to be
// said before the note is saved rather than discovered by an appointment that
// never arrived.
import test from "node:test";
import assert from "node:assert/strict";

import { hasTime, timeHint } from "./schedule.js";

test("a note without a time shows no time", () => {
  assert.equal(hasTime({}), false);
  assert.equal(hasTime({ at: "14:30" }), false, "the raw field is the server's business");
  assert.equal(hasTime({ at: "14:30", time_label: "14:30" }), true);
  assert.equal(hasTime(null), false);
});

test("no time, nothing to say", () => {
  assert.equal(timeHint("", 1), "");
  assert.equal(timeHint("", 0), "");
});

test("a time with no date says it will not fire", () => {
  // The whole reason this module exists.
  assert.match(timeHint("14:30", 0), /will not fire/);
});

test("one day: it fires at the time", () => {
  assert.equal(timeHint("09:05", 1), "fires at 09:05");
});

test("several days: it says how many, because that is not obvious either", () => {
  assert.equal(timeHint("14:30", 3), "fires at 14:30, on each of those 3 days");
});

test("the hint does not pluralise a single day into \"1 days\"", () => {
  assert.doesNotMatch(timeHint("14:30", 1), /1 days/);
});
