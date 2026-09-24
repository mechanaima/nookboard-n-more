import assert from "node:assert/strict";
import test from "node:test";

import {
  checkpointLabel, deletedLine, restoreTitle, versionLabel, whenLabel,
} from "./history.js";


test("the newest version is called what you have, but only if it is", () => {
  // is_now is measured by the server, so the client does not have to guess and
  // cannot disagree with the file on disk.
  assert.equal(versionLabel({ words: "2 hours ago", is_now: true }), "what you have");
  assert.equal(versionLabel({ words: "2 hours ago", is_now: false }), "2 hours ago");
  // A version whose date could not be read still says something true.
  assert.equal(versionLabel({ words: "at some point" }), "at some point");
  assert.equal(versionLabel(null), "at some point");
});

test("a change from today shows the clock, older changes show the distance", () => {
  const change = { when: "2026-09-24T15:07:00-04:00", clock: "15:07", words: "just now" };
  assert.equal(whenLabel(change, "2026-09-24"), "15:07");
  // A different day is a distance, not a clock. The day is read from the
  // change's *own* stamp, offset and all, which is why this comparison is a
  // string compare of dates rather than a Date subtraction.
  assert.equal(whenLabel(change, "2026-09-25"), "just now");
  assert.equal(whenLabel(change, "2026-09-23"), "just now");
  // Without a today to compare against, a row must not claim a clock it cannot
  // place -- it says the words instead.
  assert.equal(whenLabel(change), "just now");
  assert.equal(whenLabel({ when: change.when, words: "2 days ago" }, "2026-09-24"), "2 days ago");
  assert.equal(whenLabel({ when: change.when }, "2026-09-24"), "at some point");
  assert.equal(whenLabel(null, "2026-09-24"), "at some point");
});

test("a version from today is named by its clock, unless it is the one you have", () => {
  const old = { is_now: false, when: "2026-09-24T09:12:00-04:00", clock: "09:12", words: "just now" };
  assert.equal(versionLabel(old, "2026-09-24"), "09:12");
  assert.equal(versionLabel(old, "2026-09-20"), "just now");
  // "what you have" beats the clock: it is the more useful fact about that row
  assert.equal(versionLabel({ ...old, is_now: true }, "2026-09-24"), "what you have");
});

test("a restore says what it will write, and where", () => {
  assert.equal(
    restoreTitle({ words: "3 hours ago" }, "inbox/soil.md"),
    "write 3 hours ago back into inbox/soil.md",
  );
  // No path, no lie about one.
  assert.equal(restoreTitle({ words: "3 hours ago" }), "write 3 hours ago back into this note");
  assert.equal(restoreTitle({}), "write an older version back into this note");
});

test("a lost note reads as its path and how long it has been gone", () => {
  assert.equal(deletedLine({ path: "inbox/gone.md", words: "2 days ago" }), "inbox/gone.md · 2 days ago");
  assert.equal(deletedLine({ path: "inbox/gone.md" }), "inbox/gone.md");
  assert.equal(deletedLine(null), "");
});

test("the button says how many changes it would record", () => {
  assert.equal(checkpointLabel(0), "Nothing to record");
  assert.equal(checkpointLabel(1), "Record this change");
  assert.equal(checkpointLabel(4), "Record these 4 changes");
});
