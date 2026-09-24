// node --test — pure board helpers.
import test from "node:test";
import assert from "node:assert/strict";

import {
  STAGES, stageIndex, shiftStage, dropBeforeId, blocksLabel, blockedLabel,
  blockerChipLabel, summaryText, completionWarning, resolveTaskRef, depCandidates,
  isOpenTask,
} from "./board.js";

test("stages run left to right ending at done", () => {
  assert.deepEqual(STAGES.map((s) => s.id), ["backlog", "todo", "doing", "review", "done"]);
});

test("stageIndex finds the column", () => {
  assert.equal(stageIndex("doing"), 2);
  assert.equal(stageIndex("nope"), -1);
});

test("shiftStage steps one column", () => {
  assert.equal(shiftStage("todo", 1), "doing");
  assert.equal(shiftStage("doing", -1), "todo");
});

test("shiftStage stops at the ends instead of wrapping", () => {
  // Wrapping would silently send a card from Done to Backlog on a stray tap.
  assert.equal(shiftStage("backlog", -1), null);
  assert.equal(shiftStage("done", 1), null);
});

test("shiftStage ignores an unknown column", () => {
  assert.equal(shiftStage("someday", 1), null);
});

test("dropBeforeId returns the card the pointer is above", () => {
  const mids = [{ id: "a", mid: 100 }, { id: "b", mid: 200 }, { id: "c", mid: 300 }];
  assert.equal(dropBeforeId(mids, 50), "a");
  assert.equal(dropBeforeId(mids, 150), "b");
  assert.equal(dropBeforeId(mids, 250), "c");
});

test("dropBeforeId appends below the last card", () => {
  const mids = [{ id: "a", mid: 100 }, { id: "b", mid: 200 }];
  assert.equal(dropBeforeId(mids, 999), null);
});

test("dropBeforeId on an empty column appends", () => {
  assert.equal(dropBeforeId([], 10), null);
});

test("blocksLabel is empty when nothing waits on the card", () => {
  assert.equal(blocksLabel(0), "");
  assert.equal(blocksLabel(1), "blocks 1");
  assert.equal(blocksLabel(3), "blocks 3");
});

test("blockedLabel names a single blocker", () => {
  const card = { open_blockers: [{ title: "Mix the soil", missing: false }] };
  assert.equal(blockedLabel(card), "blocked by “Mix the soil”");
});

test("blockedLabel counts multiple blockers", () => {
  const card = { open_blockers: [{ title: "a" }, { title: "b" }] };
  assert.equal(blockedLabel(card), "blocked by 2 tasks");
});

test("blockedLabel calls out a blocker that was deleted", () => {
  const card = { open_blockers: [{ id: "ghost", title: "ghost", missing: true }] };
  assert.equal(blockedLabel(card), "waiting on a deleted task");
});

test("blockedLabel is empty for an unblocked card", () => {
  assert.equal(blockedLabel({ open_blockers: [] }), "");
  assert.equal(blockedLabel({}), "");
  assert.equal(blockedLabel(undefined), "");
});

test("blockerChipLabel marks a missing note", () => {
  assert.equal(blockerChipLabel({ title: "Ship it", missing: false }), "Ship it");
  assert.equal(blockerChipLabel({ id: "ghost", missing: true }), "ghost (deleted)");
});

test("summaryText reads as a status line", () => {
  assert.equal(
    summaryText({ total: 5, open: 4, done: 1, blocked: 2, ready: 2 }),
    "2 ready · 2 blocked · 1/5 done",
  );
});

test("summaryText omits the blocked clause when nothing is blocked", () => {
  assert.equal(
    summaryText({ total: 3, open: 3, done: 0, blocked: 0, ready: 3 }),
    "3 ready · 0/3 done",
  );
});

test("summaryText handles an empty board", () => {
  assert.equal(summaryText({ total: 0, open: 0, done: 0, blocked: 0, ready: 0 }), "no tasks yet");
  assert.equal(summaryText(null), "");
});

test("summaryText says all clear when nothing is open", () => {
  assert.equal(
    summaryText({ total: 2, open: 0, done: 2, blocked: 0, ready: 0 }),
    "0 ready · 2/2 done · all clear",
  );
});

test("completionWarning stays quiet for an unblocked task", () => {
  assert.equal(completionWarning({ open_blockers: [] }), "");
  assert.equal(completionWarning({}), "");
});

test("completionWarning names what is still outstanding", () => {
  const card = { open_blockers: [{ title: "Mix the soil", missing: false }] };
  assert.match(completionWarning(card), /still blocked by “Mix the soil”/);
  assert.match(completionWarning(card), /anyway\?/);
});

test("completionWarning describes a deleted blocker", () => {
  const card = { open_blockers: [{ id: "g", missing: true }] };
  assert.match(completionWarning(card), /a deleted task/);
});

// -- resolving the dependency box -------------------------------------------

const TASKS = [
  { id: "t1", title: "Mix the soil" },
  { id: "t2", title: "Buy perlite" },
  { id: "t3", title: "Repot the monstera" },
];

test("resolveTaskRef matches an exact id", () => {
  assert.equal(resolveTaskRef(TASKS, "t2"), "t2");
});

test("resolveTaskRef matches an exact title, ignoring case and padding", () => {
  assert.equal(resolveTaskRef(TASKS, "  buy perlite "), "t2");
  assert.equal(resolveTaskRef(TASKS, "MIX THE SOIL"), "t1");
});

test("resolveTaskRef accepts a unique prefix", () => {
  assert.equal(resolveTaskRef(TASKS, "repot"), "t3");
});

test("resolveTaskRef refuses an ambiguous prefix", () => {
  // "re" could be "Repot the monstera" only, but adding a second match must not
  // silently pick one.
  const ambiguous = [...TASKS, { id: "t4", title: "Refill the water" }];
  assert.equal(resolveTaskRef(ambiguous, "re"), null);
});

test("resolveTaskRef returns null for nonsense instead of guessing", () => {
  assert.equal(resolveTaskRef(TASKS, "sand the floor"), null);
  assert.equal(resolveTaskRef(TASKS, ""), null);
  assert.equal(resolveTaskRef(TASKS, "   "), null);
  assert.equal(resolveTaskRef(null, "anything"), null);
});

test("depCandidates excludes the open note and its existing blockers", () => {
  const ids = depCandidates(TASKS, "t1", ["t2"]).map((t) => t.id);
  assert.deepEqual(ids, ["t3"]);
});

test("depCandidates handles an empty blocker list", () => {
  assert.equal(depCandidates(TASKS, "t1", []).length, 2);
  assert.deepEqual(depCandidates(null, "t1", []), []);
});

test("isOpenTask treats finished and dropped tasks as closed", () => {
  assert.equal(isOpenTask({ status: "open" }), true);
  assert.equal(isOpenTask({ status: "migrated" }), true);
  assert.equal(isOpenTask({ status: "scheduled" }), true);
  assert.equal(isOpenTask({ status: "complete" }), false);
  assert.equal(isOpenTask({ status: "irrelevant" }), false);
  assert.equal(isOpenTask({}), true);
  assert.equal(isOpenTask(null), true);
});

test("a finished task is not offered as a blocker", () => {
  // It cannot gate anything, so listing it would just be noise.
  const tasks = [
    { id: "a", title: "Still going", status: "open" },
    { id: "b", title: "All done", status: "complete" },
  ];
  const offered = depCandidates(tasks.filter(isOpenTask), "x", []).map((t) => t.id);
  assert.deepEqual(offered, ["a"]);
  // ...but it is still resolvable, so the UI can explain why it was refused.
  assert.equal(resolveTaskRef(tasks, "All done"), "b");
});
