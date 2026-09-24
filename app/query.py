"""Queries you can write into a note.

A template expands once and freezes. A query is resolved every time you look at
the note, which is the difference between writing down what you finished this
week and writing down how to find it.

The reference day comes from the note the query sits in rather than from the
clock, so a note about last week still says last week when you open it in March.

What counts as finished is `daily.completed_on` -- the same function the daily
and weekly rollups use -- so a query and the generated notes cannot disagree
about it.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from . import daily, weekly
from .models import Note, Signifier, Status, iso_week_bounds

#: The fence a query is written in, as ```nookboard ... ```
LANGUAGE = "nookboard"

WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

#: Periods that need no argument. Anything else is `on <iso date>` or
#: `in <iso week>`.
NAMED_PERIODS = (
    "today", "yesterday", "this week", "last week", "this month", "last month",
)

#: Shown when a query cannot be understood. A query that silently renders
#: nothing is worse than one that says it did not follow you -- you would go on
#: believing the note was empty rather than that the query was wrong.
EXPECTED = "`completed this week`, `days this week`, or `open tasks`"


class QueryError(ValueError):
    """A query that cannot be understood, or asked for a period that cannot."""


@dataclass(frozen=True)
class Query:
    verb: str                      # "days" | "completed" | "open"
    period: Optional[str] = None
    source: str = ""


def parse(text: str) -> Query:
    """Read one query. Raises `QueryError` rather than guessing."""
    source = " ".join((text or "").split()).strip()
    raw = source.lower()
    if not raw:
        raise QueryError("that query is empty")

    if raw in ("open", "open tasks"):
        return Query(verb="open", source=source)

    for verb in ("days", "completed"):
        if raw == verb:
            raise QueryError(f"`{verb}` needs to know when — {EXPECTED}")
        if raw.startswith(f"{verb} "):
            # Sliced from the original, not the lower-cased copy: the keyword is
            # matched in any case, but an ISO week is written `2026-W01`, and
            # echoing `2026-w01` back at you spells a week nobody writes.
            period = source[len(verb) :].strip()
            if not period:
                raise QueryError(f"`{verb}` needs to know when — {EXPECTED}")
            return Query(verb=verb, period=period, source=source)

    raise QueryError(f"don't know how to read `{raw}` — {EXPECTED}")


def bounds(period: str, on: date) -> tuple[date, date]:
    """The days a period covers, relative to `on`.

    Relative to a day rather than a clock time on purpose: `on` is the date of
    the note the query sits in, so `this week` in a note about last week means
    last week. Anything else would make an old note quietly lie.
    """
    period = " ".join((period or "").split()).strip().lower()
    if not period:
        raise QueryError(f"no period given — {EXPECTED}")

    if period == "today":
        return on, on
    if period == "yesterday":
        day = on - timedelta(days=1)
        return day, day
    if period == "this week":
        return iso_week_bounds(on)
    if period == "last week":
        monday, sunday = iso_week_bounds(on)
        return monday - timedelta(days=7), sunday - timedelta(days=7)
    if period in ("this month", "last month"):
        anchor = on if period == "this month" else on.replace(day=1) - timedelta(days=1)
        first = anchor.replace(day=1)
        last = first.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
        return first, last

    if period.startswith("on "):
        day = _iso(period[3:].strip())
        return day, day
    if period.startswith("in "):
        try:
            return weekly.week_bounds(period[3:].strip().upper())
        except (TypeError, ValueError):
            raise QueryError(
                f"`{period[3:].strip()}` is not an ISO week — that looks like `in 2026-W39`"
            )

    raise QueryError(
        f"don't know the period `{period}` — try {EXPECTED}, "
        "`on 2026-09-24`, or `in 2026-W39`"
    )


def _iso(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise QueryError(f"`{text}` is not a date — that looks like `on 2026-09-24`")


def completed_by_day(
    notes: Sequence[Note], start: date, end: date
) -> dict[date, list[Note]]:
    """What was finished each day of a span, days with nothing omitted.

    The generic form of `weekly.finished_in`: the same loop, over any span. Both
    ask `daily.completed_on`, so the rule is shared even where the code is not.
    """
    out: dict[date, list[Note]] = {}
    day = start
    while day <= end:
        done = daily.completed_on(notes, day)
        if done:
            out[day] = done
        day += timedelta(days=1)
    return out


def resolve(query: Query, notes: Sequence[Note], *, on: date) -> str:
    """The markdown a query stands for, right now."""
    if query.verb == "open":
        return _open_markdown(notes)
    if query.verb not in ("days", "completed"):
        raise QueryError(f"don't know how to read `{query.source}` — {EXPECTED}")

    start, end = bounds(query.period or "", on)

    if query.verb == "days":
        return _days_markdown(start, end)

    done_by_day = completed_by_day(notes, start, end)
    single = start == end
    if not done_by_day:
        # Said, not left blank: an empty gap looks like a query that failed.
        return f"_Nothing finished {_span_label(query, start, end)}._"
    if single:
        return "\n".join(f"- [[{n.title}]]" for n in done_by_day[start])
    # Same shape as the weekly rollup's Finished list, so a query block and the
    # generated note above it read alike instead of looking like two features.
    lines = []
    for day in sorted(done_by_day):
        titles = ", ".join(f"[[{n.title}]]" for n in done_by_day[day])
        lines.append(f"- **{day.strftime('%a')} {day.day}** \u2014 {titles}")
    return "\n".join(lines)


def _span_label(query: Query, start: date, end: date) -> str:
    """How to say which span a query covered, mid-sentence.

    The label carries its own preposition, so nothing can print "in in". A
    one-day span is named by the date it actually covered -- however it was
    asked for, since the date is the part you can check -- except for `today`
    and `yesterday`, which read better as the word you wrote than as a date you
    would have to translate. A longer span keeps your own phrasing, so the note
    reads back what you asked rather than a range you would have to decode.
    """
    if start == end:
        if (query.period or "").lower() in ("today", "yesterday"):
            return (query.period or "").lower()
        return f"on {start.isoformat()}"
    return query.period or f"{start.isoformat()} to {end.isoformat()}"


def _days_markdown(start: date, end: date) -> str:
    """The days of a span, linked, so a weekly note is a way into each day."""
    lines = []
    day = start
    while day <= end:
        lines.append(f"- [[{day.isoformat()}]] {WEEKDAY_NAMES[day.weekday()]}")
        day += timedelta(days=1)
    return "\n".join(lines)


def _open_markdown(notes: Sequence[Note]) -> str:
    open_tasks = [
        n for n in notes
        if n.signifier is Signifier.TASK and n.status is Status.OPEN
    ]
    if not open_tasks:
        return "_Nothing open._"
    open_tasks.sort(key=lambda n: (n.title.lower(), n.id))
    return "\n".join(f"- [[{n.title}]]" for n in open_tasks)


def render(text: str, notes: Sequence[Note], *, on: date) -> str:
    """Resolve a query, or say why it could not be.

    Never raises: this is called to fill a note you are reading, and a note has
    to render whatever is in it.
    """
    try:
        return resolve(parse(text), notes, on=on)
    except QueryError as exc:
        return f"> **Query not understood** — {exc}"


def render_body(body: str, notes: Sequence[Note], *, on: date) -> str:
    """Replace every query block in a note body with what it resolves to.

    The server-side counterpart of the browser's splicing, used by exports and by
    anything that needs a note's text without a browser to render it.
    """
    out, cursor = [], 0
    for start, end, text in find_blocks(body):
        out.append(body[cursor:start])
        out.append(render(text, notes, on=on))
        cursor = end
    out.append(body[cursor:])
    return "".join(out)


#: ```nookboard <query> ``` — the language name is what makes it a query rather
#: than a piece of code you happened to fence.
FENCE_RE = re.compile(
    r"^[ \t]*```[ \t]*" + LANGUAGE + r"[ \t]*\r?\n(.*?)^[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)


def find_blocks(body: str) -> list[tuple[int, int, str]]:
    """Every query block in a body: (start, end, query text)."""
    return [
        (m.start(), m.end(), m.group(1).strip())
        for m in FENCE_RE.finditer(body or "")
    ]
