// The words the Bookmarks view shows.
//
// Every *fact* -- how many, what host a link goes to, why an address cannot be
// opened, what a check found and how long ago -- comes from the server
// (`app/bookmarks.py`, `app/health.py`, `app/health_run.py`). Nothing here parses a
// url, counts a list, or works out an age, because a second opinion about a link, a
// total, or a duration is a second thing that can be wrong.

/** The line above the list: how many, how many are useless, and when it was checked. */
export function countLine(payload, check) {
  const usable = (payload.count || 0) - (payload.unusable || 0);
  const bits = [];
  if (usable || payload.unusable) {
    bits.push(`${usable} bookmark${usable === 1 ? "" : "s"}`);
    if (payload.unusable) bits.push(`${payload.unusable} that cannot be opened`);
  } else {
    bits.push("Nothing bookmarked yet.");
  }
  // Only a check that happened gets a time. "checked never" is worse than silence.
  if (check && check.checked_words) bits.push(`checked ${check.checked_words}`);
  return bits.join(" \u00b7 ");
}

/** What a bookmark is, for the empty state. */
export function emptyHint() {
  return "No notes point at an address yet. A note with a `url:` is a bookmark — put "
    + "a service, a page or a machine in the editor's address row and it shows up "
    + "here, grouped by its first tag.";
}

/** The chip on one card: the server's word for it, or the word for not knowing. */
export function statusWord(result) {
  return (result && result.words) || "not checked";
}

/** The `title` on a chip: what was found, and how long it took to find out.
 *
 * Kept as a tooltip rather than drawn on the card because a status is a fact with a
 * timestamp -- it belongs where it can say *when*, which is one hover away.
 */
export function statusTitle(result) {
  if (!result) return "not checked yet";
  const bits = [result.words];
  if (result.why) bits.push(result.why);
  if (result.ms !== null && result.ms !== undefined) bits.push(`${result.ms}ms`);
  if (result.at) bits.push(`at ${result.at}`);
  return bits.join(" \u2014 ");
}
