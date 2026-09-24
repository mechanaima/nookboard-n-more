"""Daily notes: the day's completed work, summarised in place.

A summary is a *generated section* inside an ordinary note rather than a note of
its own, fenced by markers. That is what makes regenerating it safe: the day's
note is something you also write in, so a second run has to replace exactly what
it generated last time and not one line more.

Nothing here talks to the model or the vault; the prompt lives in `ai` and the
writing lives in `main`. This module is only the rules about what a day
contains and how a section goes back into a file.
"""
from __future__ import annotations

from datetime import date, timedelta

from .models import Note, Signifier, Status

#: HTML comments so the fences never render in Obsidian or in the app.
MARK_START = "<!-- nookboard:daily:start -->"
MARK_END = "<!-- nookboard:daily:end -->"

#: How far back a missed end-of-day run will reach. One day: a laptop closed for
#: a fortnight should not wake up and spend fifteen model calls summarising
#: afternoons nobody is going to read.
CATCHUP_DAYS = 1


def daily_note_id(day: date) -> str:
    """The note id for a day. Derived from the date so it cannot drift."""
    return f"daily-{day.isoformat()}"


def completed_on(notes: list[Note], day: date) -> list[Note]:
    """Notes finished on `day`, in a stable order.

    Only Status.COMPLETE counts. A struck-through note sits in the Done column
    but was abandoned rather than finished, and a summary of the day's work that
    counts abandoned things is worse than no summary.
    """
    done = [n for n in notes if n.status is Status.COMPLETE and n.completed == day]
    return sorted(done, key=lambda n: (n.title or "").lower())


def render(day: date, done: list[Note], recap: str | None) -> str:
    """The generated section for a day.

    The list is the part that is always true and always present; `recap` is
    model prose that may be missing. Losing the prose degrades the section, it
    does not empty it — the point of the note is to record what got done.
    """
    lines = [MARK_START, "", f"## Done {day.isoformat()}", ""]
    if recap:
        lines += [recap.strip(), ""]
    if done:
        for n in done:
            meta = [part for part in (n.collection, ", ".join(n.tags)) if part]
            suffix = f" — {meta[0]}" if meta and n.collection != "inbox" else ""
            lines.append(f"- {n.title}{suffix}")
    else:
        lines.append("_Nothing was marked done._")
    lines += ["", MARK_END]
    return "\n".join(lines)


def upsert_section(body: str, section: str) -> str:
    """Put `section` into `body`, replacing any previous generated section.

    Appending instead of replacing would leave a second copy of the summary
    every time the run fires, so the markers are treated as the boundary of
    something this program owns. Everything outside them — including text added
    after them — is preserved byte for byte.
    """
    body = body or ""
    start = body.find(MARK_START)
    if start != -1:
        end = body.find(MARK_END, start)
        if end == -1:
            # A half-deleted fence: repair it by rewriting to the end rather
            # than appending a second, overlapping section.
            return (body[:start].rstrip("\n") + "\n\n" + section).rstrip("\n") + "\n"
        after = body[end + len(MARK_END):]
        head = body[:start].rstrip("\n")
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


def has_summary(body: str) -> bool:
    """Whether a note already carries a generated section."""
    return MARK_START in (body or "")


def is_due(now, hour: int) -> bool:
    """Whether the end-of-day run has come round.

    A single hour, not a cron expression: this fires once a day on a laptop that
    is asleep most of the evening, so the interesting question is whether the
    cutoff has passed at all, not whether the clock is exactly on it.
    """
    return now.hour >= hour


def pending_days(
    notes: list[Note],
    *,
    today: date,
    hour: int,
    now,
    blocked: set[date] | None = None,
) -> list[date]:
    """Days whose summary is owed and can still be written.

    `today` is only owed once its cutoff has passed. Yesterday is owed whether
    or not anyone was watching at the time, which is the case that matters for
    a local app: the machine is shut at 22:00 far more often than it is open.
    """
    blocked = blocked or set()
    days: list[date] = []
    candidates = [today - timedelta(days=n) for n in range(CATCHUP_DAYS + 1)]
    for day in candidates:
        if day in blocked:
            continue
        if day == today and not is_due(now, hour):
            continue
        if not completed_on(notes, day):
            # Never invent a note for a day with nothing in it.
            continue
        days.append(day)
    return sorted(days)


def note_for(day: date, done: list[Note], recap: str | None, *, created: date | None = None) -> Note:
    """The daily note itself, as a fresh note.

    Dated on the day it covers so it lands in the calendar and the timeline next
    to whatever else happened, with the title left as the bare ISO date: that is
    what makes `[[2026-09-23]]` resolve, and a prettier title would break it.
    """
    return Note(
        id=daily_note_id(day),
        collection="daily",
        title=day.isoformat(),
        body=upsert_section("", render(day, done, recap)),
        signifier=Signifier.NOTE,
        status=Status.OPEN,
        dates=[day],
        created=created or day,
    )
