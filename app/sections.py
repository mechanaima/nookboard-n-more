"""Generated sections inside notes you also write in.

A summary is a *fenced region* of an ordinary note rather than a note of its own,
and that is the whole trick: it makes regenerating one safe. The day's note is
somewhere you write too, so a second run has to replace exactly what it generated
last time and not one line more. Everything outside the markers — including text
added after them — survives byte for byte.

Shared by the daily and weekly summaries, which are the same operation at two
scales and would otherwise each carry their own copy of this.
"""

from __future__ import annotations


def mark(scope: str, edge: str) -> str:
    """The fence for a scope (`daily`, `weekly`) and an edge (`start`, `end`).

    HTML comments, so the fence never renders in Obsidian or in the app.
    """
    return f"<!-- nookboard:{scope}:{edge} -->"


def read(body: str, *, start: str, end: str) -> str | None:
    """The text inside a fenced region, or None if the fence is not both there.

    The inverse of `upsert`, and it lives here for the same reason: the fence
    format is this module's to own, so a reader that spelled the markers itself
    could drift from the writer. A half-deleted fence returns None rather than a
    guess -- the caller can then treat the region as absent, which is true.
    """
    body = body or ""
    opened = body.find(start)
    if opened == -1:
        return None
    closed = body.find(end, opened + len(start))
    if closed == -1:
        return None
    return body[opened + len(start) : closed].strip()


def upsert(body: str, section: str, *, start: str, end: str) -> str:
    """Put `section` into `body`, replacing any previous generated section.

    Appending instead of replacing would leave a second copy of the summary
    every time the run fires, so the markers are treated as the boundary of
    something this program owns.
    """
    body = body or ""
    found = body.find(start)
    if found != -1:
        close = body.find(end, found)
        if close == -1:
            # A half-deleted fence: repair it by rewriting to the end rather
            # than appending a second, overlapping section.
            return (body[:found].rstrip("\n") + "\n\n" + section).rstrip("\n") + "\n"
        after = body[close + len(end):]
        head = body[:found].rstrip("\n")
        tail = after.strip("\n")
        out = head
        if out:
            out += "\n\n"
        out += section.rstrip("\n")
        if tail:
            out += "\n\n" + tail
        return out + "\n"
    if not body.strip():
        return section.rstrip("\n") + "\n"
    return body.rstrip("\n") + "\n\n" + section.rstrip("\n") + "\n"
