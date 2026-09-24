"""When a note is *at* a time, and what that means.

A note has carried a **day** since the beginning. `dates:` is a list of days,
and everything downstream treats a note as belonging to the whole of each of
them: the timeline lists it under a day, the calendar shows it in a day cell, a
day's recap counts it, and the ICS feed publishes it as an all-day event. That
is right for a thought, a deadline, a thing that happened.

A **time** is a different kind of fact. It names an instant, which means it can
be *passed* — and something can happen when it does. A note with a time is an
appointment; a note without one is a day.

This module is where that distinction lives. It is small, pure, and free of
I/O, because three callers need the same answers and must not each invent
them:

- the ICS feed, which emits a timed `VEVENT` instead of a `VALUE=DATE` one
- the calendar view, which puts the time beside the event
- the notifier, which fires when the instant arrives

The rules, stated plainly:

- A note's time applies to **every** date it carries. A workshop listed on
  three days at 14:00 is at 14:00 on all three.
- A time with **no date** is not scheduled. There is no instant to name, so
  nothing fires and no event is emitted — the note still shows the time, so
  what you wrote is never thrown away, but the app does not pretend to be
  waiting for it.
- An end earlier than its start **crosses midnight** (23:00–01:00 is two hours
  long, not minus twenty-two).
- No timezone. Everything here is naive local time, and the ICS feed emits
  *floating* time (no `Z`, no `TZID`), which RFC 5545 defines as "the time as
  the viewer's clock reads it". That is the honest thing for a personal feed
  that lives on the machine that writes it; a traveling laptop changes meaning
  either way, and pretending to a timezone would only move the lie.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time as clock, timedelta
from typing import TYPE_CHECKING, Iterable, Iterator, NamedTuple, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, so models can import us
    from .models import Note

#: How long a timed note lasts when it does not say. A time without an end is
#: still an appointment, and an hour is the shortest span that is not a guess
#: dressed up as a fact.
DEFAULT_DURATION = timedelta(hours=1)


def parse_time(raw: object) -> Optional[str]:
    """Normalize a clock time to `"HH:MM"`, or `None` if that is not what it is.

    Tolerant about the shapes a person actually writes — `9:05`, `09:05`,
    `09:05:00`, `0905` — and strict about what it means: no seconds survive,
    because a minute is the resolution anything here acts on. `25:00` is not a
    time and is not quietly rounded into one; it is dropped, the same way an
    unparseable date is, so a typo cannot schedule something at the wrong hour.

    **The integer case is YAML, not us.** `at: 14:30` written by hand is not a
    string to a YAML loader — the colon makes it base-60, so PyYAML hands over
    the integer 870. Dropping that would make a hand-written time vanish
    silently, and reading it as "870 minutes" would be worse, so it is decoded
    the way it was encoded: base 60, `divmod(870, 60)` → `14:30`.

    The one shape this cannot resolve is a bare `at: 0:5`, which YAML also
    encodes as the integer 5 and which is then read as the hour (09:00-style)
    rather than as five past midnight. Zero-padding is the app's own output, so
    nothing this app writes is ever ambiguous -- see `Note.to_markdown`.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):  # YAML will happily hand us a datetime
        return raw.strftime("%H:%M")
    if isinstance(raw, clock):
        return raw.strftime("%H:%M")
    if isinstance(raw, bool):  # `at: yes` is not a time
        return None
    if isinstance(raw, int):
        # 0-23 can only be a bare hour: a colon form that small ("0:05") stays a
        # string through YAML, so an integer here was written as `at: 9`.
        if 0 <= raw <= 23:
            return f"{raw:02d}:00"
        if raw > 23:
            hour, minute = divmod(raw, 60)
            return f"{hour:02d}:{minute:02d}" if hour <= 23 and minute <= 59 else None
        return None

    text = str(raw).strip()
    if not text:
        return None

    # `0905` and `9` are accepted only as whole digits. `9.30` is not a time in
    # any convention this app should guess at.
    digits = text.replace(":", "")
    if text.isdigit() and len(digits) in (3, 4):
        text = f"{digits[:-2]}:{digits[-2:]}"
    elif text.isdigit() and len(digits) <= 2:
        text = f"{digits}:00"

    parts = text.split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _clock(value: Optional[str]) -> Optional[clock]:
    parsed = parse_time(value)
    if parsed is None:
        return None
    hour, minute = parsed.split(":")
    return clock(int(hour), int(minute))


def raw_time_from_frontmatter(md: str, key: str) -> Optional[str]:
    """The `key:` scalar exactly as written, or `None` if it is not there.

    YAML is lossy about times in a way that matters here. An unquoted `14:30`
    is base-60 and arrives as the integer 870; an unquoted `0930` arrives as the
    integer 930, which reads as **15:30** if you decode it base-60 — a different
    time, six hours out, and nothing downstream can tell the two apart. A bare
    `9` is the integer 9 either way.

    Both spellings are things a person types straight into the frontmatter, and
    the app's own output is zero-padded and quoted so it is never ambiguous.
    The raw text therefore wins when it can be read, and the parsed value is the
    fallback for anything this scan cannot make sense of — a YAML construct we
    do not recognise costs us nothing, because `parse_time` still gets a say.
    """
    lines = md.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.*?)\s*$")
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.lstrip().startswith("#"):
            continue
        found = pattern.match(line)
        if not found:
            continue
        value = found.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value or None
    return None


def is_timed(note: "Note") -> bool:
    """Does this note name an instant, or only a day?"""
    return _clock(getattr(note, "at", None)) is not None


def label(note: "Note", separator: str = "\u2013") -> str:
    """The time as a person reads it: `14:30` or `14:30–16:00`, or `""`.

    The end is shown only when the note names one. Otherwise the default
    duration is still what the ICS feed and the notifier use, but printing an
    invented `15:30` next to your event would read as something you wrote.
    """
    start = _clock(getattr(note, "at", None))
    if start is None:
        return ""
    end = _clock(getattr(note, "until", None))
    if end is None:
        return start.strftime("%H:%M")
    return f"{start.strftime('%H:%M')}{separator}{end.strftime('%H:%M')}"


def starts_at(note: "Note", day: date) -> Optional[datetime]:
    """The instant this note begins on `day`, or `None` if it has no time."""
    start = _clock(getattr(note, "at", None))
    if start is None:
        return None
    return datetime.combine(day, start)


def ends_at(note: "Note", day: date) -> Optional[datetime]:
    """The instant it ends. `until` if given, else the default duration.

    An end at or before the start crosses midnight rather than collapsing: a
    shift written `23:00`–`01:00` is two hours long.
    """
    start = starts_at(note, day)
    if start is None:
        return None
    end = _clock(getattr(note, "until", None))
    if end is None:
        return start + DEFAULT_DURATION
    finish = datetime.combine(day, end)
    if finish <= start:
        finish += timedelta(days=1)
    return finish


class Occurrence(NamedTuple):
    """One note, on one of its dates, at its time."""

    note: "Note"
    day: date
    start: datetime
    end: datetime

    @property
    def key(self) -> str:
        """What was already notified about this. Stable across restarts, and
        per-note-and-instant so editing the note does not re-fire it."""
        return f"{self.note.id}@{self.start:%Y-%m-%dT%H:%M}"


def occurrences(notes: Iterable["Note"]) -> Iterator[Occurrence]:
    """Every instant these notes name, in the order the days come.

    Untimed notes produce nothing: they belong to a whole day, and a whole day
    is not something to interrupt anyone about.
    """
    for note in notes:
        for day in note.dates:
            start = starts_at(note, day)
            if start is None:
                continue
            # `ends_at` is None only when `starts_at` is, which was just ruled out.
            yield Occurrence(note, day, start, ends_at(note, day) or start + DEFAULT_DURATION)


def due(
    notes: Iterable["Note"],
    now: datetime,
    *,
    fired: Iterable[str] = (),
    since: Optional[datetime] = None,
) -> list[Occurrence]:
    """What should fire now: timed, already started, not already announced.

    `since` bounds how far back to reach. Without it, a machine that was off for
    a week would greet its owner with every appointment it slept through, which
    is not a reminder -- it is an accusation. Callers pass the start of today,
    so a missed appointment is caught up on the day it belonged to and forgotten
    after that.
    """
    seen = set(fired)
    floor = since
    found = [
        occ
        for occ in occurrences(notes)
        if occ.start <= now and (floor is None or occ.start >= floor) and occ.key not in seen
    ]
    # Oldest first: if three things were missed, the order they happened in is
    # the only order that makes sense to read them in.
    found.sort(key=lambda o: o.start)
    return found


def how_late(start: datetime, now: datetime) -> Optional[str]:
    """`None` when it is not late, else how late in words.

    Being told at 14:31 that something started at 14:30 needs no comment. Being
    told at 16:00 that it started at 14:30 does, or the notification reads as if
    it is happening now.
    """
    seconds = (now - start).total_seconds()
    if seconds < 120:
        return None
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minutes ago"
    if minutes < 60 * 24:
        hours, rest = divmod(minutes, 60)
        if not rest:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        return f"{hours}h {rest}m ago"
    days = (now - start).days
    return f"{days} day{'s' if days > 1 else ''} ago"


def clock_text(moment: datetime) -> str:
    """`14:30`, for a message body."""
    return moment.strftime("%H:%M")
