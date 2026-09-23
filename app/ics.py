"""Render notes as an ICS (RFC 5545) feed for external calendar apps.

The feed is meant to be SUBSCRIBED to — clients hit the URL and pull
fresh events on each visit. All-day events use DTSTART;VALUE=DATE with
DTEND set to the day after (RFC 5545 all-day convention).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from .models import Note


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
            lines += [
                "BEGIN:VEVENT",
                f"UID:{_escape(n.id)}-{d.isoformat()}@nookboard",
                f"DTSTAMP:{now}",
                f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}",
                f"SUMMARY:{_escape(n.title)}",
                f"DESCRIPTION:{_escape(n.body or '')}",
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _escape(s: str) -> str:
    return (s.replace("\\", "\\\\")
              .replace(",", "\\,")
              .replace(";", "\\;")
              .replace("\n", "\\n"))