// The words the Bookmarks view shows.
//
// Every *fact* -- how many, what host a link goes to, why an address cannot be
// opened -- comes from the server (`app/bookmarks.py`). Nothing here parses a url
// or counts a list, because a second opinion about a link is a second thing that
// can be wrong.

/** The line above the list: how many there are, and how many are useless. */
export function countLine(payload) {
  const usable = (payload.count || 0) - (payload.unusable || 0);
  if (!usable && !payload.unusable) return "Nothing bookmarked yet.";
  const bits = [`${usable} bookmark${usable === 1 ? "" : "s"}`];
  if (payload.unusable) bits.push(`${payload.unusable} that cannot be opened`);
  return bits.join(" \u00b7 ");
}

/** What a bookmark is, for the empty state. */
export function emptyHint() {
  return "No notes point at an address yet. A note with a url: is a bookmark — put a "
    + "service, a page or a machine in the editor's address row, and it shows up here, "
    + "grouped by its first tag.";
}
