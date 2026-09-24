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
from .models import Note, Signifier, Status, is_work, iso_week_bounds

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
EXPECTED = (
    "`completed this week`, `days this week`, `open tasks`, or `show notes in #mood`"
)


#: How many tags a refusal names before it says how many it is not naming.
TAG_LIST_LIMIT = 12

#: A query answers with a list, and a list of two hundred is not an answer.
SHOW_LIMIT = 25


class QueryError(ValueError):
    """A query that cannot be understood, or asked for a period that cannot."""


#: An ISO week as a period writes it. `in` introduces a period this way, which
#: is what a trailing `in <word>` has to be told apart from.
ISO_WEEK_RE = re.compile(r"(?i)^\d{4}-W\d{1,2}$")


@dataclass(frozen=True)
class Query:
    verb: str                          # "days" | "completed" | "open" | "show"
    period: Optional[str] = None
    collection: Optional[str] = None   # narrow to one collection by name
    tag: Optional[str] = None          # narrow to the notes carrying one tag
    source: str = ""


def _split_scope(rest: str) -> tuple[str, Optional[str]]:
    """Peel a trailing `in <collection>` off a period phrase.

    `in` is already spoken for -- `in 2026-W39` is a period -- so the two are
    told apart by what follows it: an ISO week makes a period, anything else can
    only be a collection. `completed in 2026-W39` must not become a search for a
    collection called 2026-W39.
    """
    match = re.search(r"(?i)\s+in\s+(\S.*)$", rest)
    if match and not ISO_WEEK_RE.match(match.group(1).strip()):
        return rest[: match.start()].strip(), match.group(1).strip()
    return rest.strip(), None


def parse(text: str) -> Query:
    """Read one query. Raises `QueryError` rather than guessing."""
    source = " ".join((text or "").split()).strip()
    raw = source.lower()
    if not raw:
        raise QueryError("that query is empty")

    # `open` optionally says `tasks`, and optionally names a collection.
    if raw == "open" or raw.startswith("open "):
        rest = source[len("open") :].strip()
        if rest[:5].lower() == "tasks":
            rest = rest[5:].strip()
        if not rest:
            return Query(verb="open", source=source)
        # `in` is required here rather than assumed: without it any stray word
        # would be read as a collection name, so `open tas` would blame the
        # vault for a typo in the keyword.
        match = re.match(r"(?i)in\s+(\S.*)$", rest)
        if not match:
            raise QueryError(
                f"don't know how to read `{source}` — try `open tasks` or "
                "`open tasks in work`"
            )
        return Query(verb="open", collection=match.group(1).strip(), source=source)

    # `show` lists notes rather than work, and says which ones: a tag or a
    # collection. It has no default, deliberately -- `show notes` on its own is
    # every note you own, which is not an answer to anything.
    if raw == "show" or raw.startswith("show "):
        rest = source[len("show") :].strip()
        if rest[:5].lower() != "notes":
            raise QueryError(
                f"don't know how to read `{source}` — try `show notes in #mood` "
                "or `show notes in journal`"
            )
        rest = rest[5:].strip()
        if not rest:
            raise QueryError(
                "`show notes` needs to know which — `show notes in #mood` for a "
                "tag, or `show notes in journal` for a collection"
            )
        if not rest[:2].lower() == "in" or rest[2:3].strip():
            raise QueryError(
                f"don't know how to read `{source}` — try `show notes in #mood` "
                "or `show notes in journal`"
            )
        scope = rest[2:].strip()
        if not scope:
            raise QueryError(
                "`show notes in` needs a tag or a collection after it"
            )
        if re.search(r"(?i)\s+in\s+\S", scope):
            raise QueryError(
                "a query can narrow by a tag or by a collection, not both yet — "
                "`show notes in #mood` or `show notes in journal`"
            )
        if scope.startswith("#"):
            tag = scope[1:].strip()
            if not tag:
                raise QueryError(
                    "`#` on its own does not name a tag — that looks like "
                    "`show notes in #mood`"
                )
            return Query(verb="show", tag=tag, source=source)
        return Query(verb="show", collection=scope, source=source)

    for verb in ("days", "completed"):
        if raw == verb:
            raise QueryError(f"`{verb}` needs to know when — {EXPECTED}")
        if raw.startswith(f"{verb} "):
            # Sliced from the original, not the lower-cased copy: the keyword is
            # matched in any case, but an ISO week is written `2026-W01`, and
            # echoing `2026-w01` back at you spells a week nobody writes.
            period, collection = _split_scope(source[len(verb) :].strip())
            if not period:
                raise QueryError(f"`{verb}` needs to know when — {EXPECTED}")
            return Query(
                verb=verb, period=period, collection=collection, source=source
            )

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
    if query.verb == "days":
        # Said rather than ignored: silently dropping a filter you asked for
        # would answer a different question than the one in the note.
        if query.collection:
            raise QueryError(
                "`days` lists the days of a span, which do not belong to a "
                "collection, so there is nothing to narrow"
            )
        start, end = bounds(query.period or "", on)
        return _days_markdown(start, end)

    pool = _pool(query, notes)

    if query.verb == "open":
        return _open_markdown(pool)
    if query.verb == "show":
        return _show_markdown(pool, query)
    if query.verb != "completed":
        raise QueryError(f"don't know how to read `{query.source}` — {EXPECTED}")

    start, end = bounds(query.period or "", on)
    done_by_day = completed_by_day(pool, start, end)
    single = start == end
    if not done_by_day:
        # Said, not left blank: an empty gap looks like a query that failed.
        return f"_Nothing finished {_span_label(query, start, end)}._"
    if single:
        return "\n".join(f"- {_link(n)}" for n in done_by_day[start])
    # Same shape as the weekly rollup's Finished list, so a query block and the
    # generated note above it read alike instead of looking like two features.
    lines = []
    for day in sorted(done_by_day):
        titles = ", ".join(_link(n) for n in done_by_day[day])
        lines.append(f"- **{day.strftime('%a')} {day.day}** \u2014 {titles}")
    return "\n".join(lines)


def _pool(query: Query, notes: Sequence[Note]) -> list[Note]:
    """The notes a query should consider.

    Naming a collection narrows to exactly that. With none named, the shapes and
    the notes the program wrote are left out: a template is a shape for other
    notes and a period note is something the app made, so neither is a thing to
    be doing -- the same set the board hides. Asking for `templates` by name is a
    deliberate act, and second-guessing it would be worse than answering.
    """
    if query.collection:
        return _named(notes, query.collection)
    if query.tag:
        return _tagged(notes, query.tag)
    return [n for n in notes if is_work(n)]


def _named(notes: Sequence[Note], wanted: str) -> list[Note]:
    """One collection by name, refusing a name this vault does not have.

    Checked against the collections that exist rather than answering with an
    empty list: a misspelled collection and an empty one look identical in a
    note, and only one of them is worth your attention.
    """
    known = sorted({n.collection for n in notes if n.collection})
    for name in known:
        if name.lower() == wanted.lower():
            return [n for n in notes if n.collection == name]
    if not known:
        raise QueryError(f"there is no `{wanted}` collection — this vault has none")
    listed = ", ".join(f"`{name}`" for name in known)
    raise QueryError(f"there is no `{wanted}` collection — you have {listed}")


def _tagged(notes: Sequence[Note], wanted: str) -> list[Note]:
    """The notes carrying one tag, refusing a tag this vault does not have.

    Matched case-insensitively but spelled the way the note spells it. A tag
    that exists *anywhere* counts as existing, even on a note the default view
    leaves out: you wrote the tag, so the tag is real, and the refusal is only
    ever about a name this vault does not have at all.

    Answered exactly like a named collection, and for the same reason -- you
    named the set yourself, so nothing about it is second-guessed.
    """
    wanted_low = wanted.lower()
    known = sorted({t for n in notes for t in n.tags}, key=str.lower)
    if not any(wanted_low == t.lower() for t in known):
        if not known:
            raise QueryError(f"there is no `#{wanted}` tag — this vault has none")
        shown = ", ".join(f"`#{t}`" for t in known[:TAG_LIST_LIMIT])
        rest = len(known) - TAG_LIST_LIMIT
        more = f" and {rest} more" if rest > 0 else ""
        raise QueryError(f"there is no `#{wanted}` tag — you have {shown}{more}")
    return [n for n in notes if any(wanted_low == t.lower() for t in n.tags)]


def _link(note: Note) -> str:
    """A note as a wikilink, or as plain text when a link cannot address it.

    A title holding `[`, `]` or a newline would end the link early, and a
    wikilink is followed by *title*, so a title that cannot be written as one
    is listed unlinked rather than as a link to something else.
    """
    title = (note.title or "").strip() or note.id
    if any(ch in title for ch in "[]\n"):
        return title
    return f"[[{title}]]"


def _recency(note: Note) -> date:
    """The day a note is from: the day it is about, else the day it was made.

    Not `created` alone. `created` is a `date`, so every note written in one
    sitting ties on it -- and a tag of today's notes would then come out in
    title order while calling itself newest-first. A note's own first date is
    the day a person means, and it is the same field the query layer already
    treats as "the day this note is about".
    """
    if note.dates and note.dates[0]:
        return note.dates[0]
    return note.created or date.min


def _show_markdown(notes: Sequence[Note], query: Query) -> str:
    """The notes a `show` asked for: newest first, and the count said out loud.

    Newest first, because a tag is usually a thread you are still adding to.
    Ordered on the server like every other ordering here, in two stable passes
    so the tie-break among one day's notes is the title ascending rather than
    the whole comparison reversed.
    """
    label = f"in #{query.tag}" if query.tag else f"in {query.collection}"
    if not notes:
        return f"_Nothing {label}._"
    ordered = sorted(notes, key=lambda n: (n.title or "").lower())
    ordered.sort(key=_recency, reverse=True)
    lines = [f"- {_link(n)}" for n in ordered[:SHOW_LIMIT]]
    rest = len(ordered) - SHOW_LIMIT
    if rest > 0:
        # A short list that does not say it is short reads as the whole set.
        lines.append(f"- _{rest} more — showing the {SHOW_LIMIT} most recent._")
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
    return "\n".join(f"- {_link(n)}" for n in open_tasks)


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
