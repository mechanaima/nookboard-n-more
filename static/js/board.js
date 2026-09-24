// Pure board helpers. No DOM — the interactive wiring lives in app.js.
//
// Column *order* is deliberately NOT reimplemented here: the server returns
// each column already sorted and this module never re-sorts it. Two sort
// implementations in two languages is two things that can disagree, and a board
// whose order changes when you reload is worse than one that cannot reorder.

export const STAGES = [
  { id: "backlog", label: "Backlog", hint: "someday / not yet" },
  { id: "todo", label: "To do", hint: "next up" },
  { id: "doing", label: "Doing", hint: "in hand" },
  { id: "review", label: "Review", hint: "checking" },
  { id: "done", label: "Done", hint: "finished" },
];

export function stageIndex(id) {
  return STAGES.findIndex((s) => s.id === id);
}

// Column one step left/right, or null at either end. Powers the keyboard- and
// tap-friendly "move card" controls: dragging is fine-motor work and a poor fit
// for a hand with arthritis, so every drag has a one-tap equivalent.
export function shiftStage(id, delta) {
  const i = stageIndex(id);
  if (i < 0) return null;
  const next = i + delta;
  if (next < 0 || next >= STAGES.length) return null;
  return STAGES[next].id;
}

// Where a card should land, given every card's vertical midpoint in the target
// column and the pointer's y. Returns the id to insert *before*, or null to
// append. `mids` must already be in display order.
export function dropBeforeId(mids, y) {
  for (const m of mids) {
    if (y < m.mid) return m.id;
  }
  return null;
}

// How many tasks a card is holding up.
export function blocksLabel(count) {
  if (!count) return "";
  return count === 1 ? "blocks 1" : `blocks ${count}`;
}

// Why a card is stuck. Names the blocker when there is exactly one, because
// "blocked by 2 tasks" makes you open the card to find out what is wrong.
export function blockedLabel(card) {
  const open = card?.open_blockers || [];
  if (!open.length) return "";
  if (open.length === 1) {
    const b = open[0];
    return b.missing ? `waiting on a deleted task` : `blocked by “${b.title}”`;
  }
  return `blocked by ${open.length} tasks`;
}

export function blockerChipLabel(b) {
  if (b.missing) return `${b.id} (deleted)`;
  return b.title;
}

// The header line: what the board is telling you at a glance.
export function summaryText(summary) {
  if (!summary) return "";
  const { total = 0, open = 0, done = 0, blocked = 0, ready = 0 } = summary;
  if (!total) return "no tasks yet";
  const parts = [`${ready} ready`];
  if (blocked) parts.push(`${blocked} blocked`);
  parts.push(`${done}/${total} done`);
  if (!open) parts.push("all clear");
  return parts.join(" · ");
}

// Drop a card into Done only after acknowledging what it was waiting on —
// finishing something while its blocker is open is usually a mistake.
export function completionWarning(card) {
  const open = card?.open_blockers || [];
  if (!open.length) return "";
  const names = open.map((b) => (b.missing ? "a deleted task" : `“${b.title}”`));
  return `This task is still blocked by ${names.join(", ")}.\n\nMark it done anyway?`;
}

// What someone typed in the dependency box -> a task id, or null.
// Tried in order: exact id, exact title, unique title prefix. An ambiguous
// prefix returns null instead of guessing — silently wiring the wrong blocker
// is worse than making them retype it.
export function resolveTaskRef(tasks, value) {
  const v = String(value || "").trim().toLowerCase();
  if (!v) return null;
  const list = tasks || [];
  const byId = list.find((t) => String(t.id).toLowerCase() === v);
  if (byId) return byId.id;

  const exact = list.filter((t) => String(t.title || "").toLowerCase() === v);
  if (exact.length === 1) return exact[0].id;
  if (exact.length > 1) return null;

  const partial = list.filter((t) => String(t.title || "").toLowerCase().startsWith(v));
  return partial.length === 1 ? partial[0].id : null;
}

// A task that is finished cannot gate anything (see the server's CLOSED rule),
// so completed tasks are deliberately not offered as blockers.
export function isOpenTask(t) {
  return !["complete", "irrelevant"].includes(String(t?.status || ""));
}

// Candidates offered in the dependency picker: everything that is not this note,
// is not already a blocker of it, and is not already finished.
export function depCandidates(tasks, noteId, blockedBy) {
  const taken = new Set(blockedBy || []);
  return (tasks || []).filter((t) => t.id !== noteId && !taken.has(t.id));
}
