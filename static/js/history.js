// History, as it is shown.
//
// Every *fact* here comes from the server: the words for when something
// happened, whether the newest version is the one on disk, which file a version
// belongs to. Nothing in this file counts, compares, or decides. There is
// already one place that does, it is Python, and it has tests.

/** When a change happened: the clock if it happened today, else how long ago.
 *
 * A list of rows that all say "just now" tells you nothing, so today gets the
 * time -- and which day a change fell on is read from the change's *own* ISO
 * stamp, which carries the offset it was recorded in. `todayIso` is the
 * browser's today, made with `localIsoDate()`; comparing the two strings avoids
 * every timezone trap that `toISOString()` walks into.
 */
export function whenLabel(entry, todayIso = "") {
  if (!entry) return "at some point";
  const day = String(entry.when || "").slice(0, 10);
  if (entry.clock && todayIso && day === todayIso) return entry.clock;
  return entry.words || "at some point";
}

/** What one version in a note's history is called. */
export function versionLabel(version, todayIso = "") {
  // Only the newest can be "what you have", and the server measured that rather
  // than assuming it: a note edited outside the app is not its own newest
  // commit, and saying otherwise would be telling someone they are looking at
  // text they are not looking at.
  if (version && version.is_now) return "what you have";
  return whenLabel(version, todayIso);
}

/** What pressing "bring this back" will do, said *before* it is pressed. */
export function restoreTitle(version, relpath = "") {
  const when = (version && version.words) || "an older version";
  return `write ${when} back into ${relpath || "this note"}`;
}

/** One lost note, as a line: which file, and how long it has been gone. */
export function deletedLine(entry) {
  if (!entry) return "";
  const path = entry.path || "";
  return entry.words ? `${path} · ${entry.words}` : path;
}

/** How many files are waiting to be recorded, as the label on a button. */
export function checkpointLabel(count) {
  if (!count) return "Nothing to record";
  return count === 1 ? "Record this change" : `Record these ${count} changes`;
}
