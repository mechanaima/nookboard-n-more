"""Render notes as an ICS (RFC 5545) feed for external calendar apps.

The feed is meant to be SUBSCRIBED to — clients hit the URL and pull
fresh events on each visit. All-day events use DTSTART;VALUE=DATE with
DTEND set to the day after (RFC 5545 all-day convention).

A note that names a **time** is published as a timed event instead: `DTSTART`
with a clock time and no `Z` and no `TZID`, which RFC 5545 calls *floating*
time — "as the viewer's clock reads it". That is the honest thing for a feed
written and read by the same machine, and a `TZID` we invented would only move
the lie. An event that runs `23:00`–`01:00` ends on the next day, and a timed
event is `TRANSP:OPAQUE` (busy) where an all-day note is `TRANSPARENT`: a
deadline does not occupy your afternoon, an appointment does.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from . import schedule
from .models import Note

#: Floating local time: no `Z` and no `TZID`, per RFC 5545.
FLOATING = "%Y%m%dT%H%M%S"


def notes_to_ics(notes: Iterable[Note]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//nookboard//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for n in notes:
        for d in n.dates:
            lines += [_event(n, d, now)]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _event(n: Note, day: date, now: str) -> str:
    start = schedule.starts_at(n, day)
    body = [
        "BEGIN:VEVENT",
        f"UID:{_escape(n.id)}-{day.isoformat()}@nookboard",
        f"DTSTAMP:{now}",
    ]
    if start is None:
        body += [
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
        ]
    else:
        end = schedule.ends_at(n, day) or start + schedule.DEFAULT_DURATION
        body += [
            f"DTSTART:{start.strftime(FLOATING)}",
            f"DTEND:{end.strftime(FLOATING)}",
        ]
    body += [
        f"SUMMARY:{_escape(n.title)}",
        f"DESCRIPTION:{_escape(n.body or '')}",
        # An instant occupies you; a day does not.
        "TRANSP:OPAQUE" if start is not None else "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]
    return "\r\n".join(body)


def _escape(s: str) -> str:
    return (s.replace("\\", "\\\\")
              .replace(",", "\\,")
              .replace(";", "\\;")
              .replace("\n", "\\n"))