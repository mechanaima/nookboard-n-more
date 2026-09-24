// reading.test.js — the properties a reading shows, and the ones it does not.
import test from "node:test";
import assert from "node:assert/strict";

import { propertyRows } from "./reading.js";

const labels = (note) => propertyRows(note).map(([k]) => k);
const value = (note, key) => (propertyRows(note).find(([k]) => k === key) || [])[1];

test("a row is drawn only when the note carries that fact", () => {
  const rows = labels({ collection: "inbox", status: "open" });
  assert.deepEqual(rows, ["collection", "status"]);
  assert.deepEqual(
    labels({ collection: "inbox", status: "open", path: null, url: "", tags: [] }),
    ["collection", "status"],
  );
});

test("the facts that are set come through as text", () => {
  const note = {
    collection: "school", status: "open", dates: ["2026-09-24", "2026-09-30"],
    time_label: "14:30 - 15:30", tags: ["alpha", "beta"], mood: "good", pain: 4,
    icon: "brain", created: "2026-09-01", completed: null,
  };
  assert.equal(value(note, "dates"), "2026-09-24, 2026-09-30");
  assert.equal(value(note, "time"), "14:30 - 15:30"); // the server's spelling, not ours
  assert.equal(value(note, "tags"), "alpha, beta");
  assert.equal(value(note, "mood"), "good");
  assert.equal(value(note, "pain"), "4");
  assert.ok(!labels(note).includes("completed"), "an unset date is not a row");
});

test("the file's own keys come last, under their own names", () => {
  const rows = propertyRows({ collection: "inbox", status: "open", frontmatter_extra: { weird: "kept" } });
  assert.deepEqual(rows[rows.length - 1], ["weird", "kept"]);
});

test("a nested value is shown as the text it is, not as nothing", () => {
  const note = { collection: "inbox", status: "open", frontmatter_extra: { plugin: { depth: 2 } } };
  assert.equal(value(note, "plugin"), JSON.stringify({ depth: 2 }));
});

test("a false or zero value is still a value", () => {
  const note = { collection: "inbox", status: "open", frontmatter_extra: { pinned: false, weight: 0 } };
  assert.equal(value(note, "pinned"), "false");
  assert.equal(value(note, "weight"), "0");
});

test("a field the server did not send is skipped, not thrown over", () => {
  const rows = propertyRows({ collection: "inbox" }); // no `status` at all
  assert.deepEqual(rows, [["collection", "inbox"]]);
});
