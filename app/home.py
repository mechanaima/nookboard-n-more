"""The dashboard: what the vault looks like from the doorway.

Every card here is a number or a small table that already exists somewhere else
-- the board's counts, the mood streak, the day's notes, the month's calendar.
Nothing is counted a second way, so the dashboard cannot disagree with the view
it stands in for. That is the whole reason this module is thin: a dashboard that
computes its own answers is a second opinion, and two opinions about your vault
is one too many.

The one thing it does not answer is the time. A clock rendered by the server is
wrong by the time you read it, so the greeting and the clock live in the browser
(`static/js/home.js`); everything that is a fact about the vault lives here.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping, Optional, Sequence

from . import daily, mood as moodlib
from .deps import board_summary, index_by_id
from .models import Note, Signifier, is_work

#: How far back the mood card looks. Long enough for a streak and an average to
#: mean something, short enough that the card describes how things are now.
MOOD_WINDOW_DAYS = 180


def vault_name(root: Path | str) -> str:
    """The vault's own name, which is what a dashboard is a view of.

    The folder's name rather than a configured title: nothing else in the app
    names the vault, and a title would be one more thing to drift from the
    directory you actually pointed at.
    """
    return Path(root).name or "vault"


def statistics(
    notes: Sequence[Note],
    *,
    collections: Optional[Sequence[str]] = None,
) -> dict:
    """What is in the vault: files, tasks, tags, collections.

    The work counts -- open, done, blocked -- are deliberately not here. They
    are on the board card, which copies them from the board's own summary, and
    two cards showing the same number is how one of them ends up disagreeing:
    an earlier version of this card counted "open" a second way and said 2
    where the board said 3, because the board counts anything not closed --
    notes included -- and the second count had quietly decided "open" meant
    open *tasks*. A card that copies an answer cannot grow its own.
    """
    return {
        "notes": len(notes),
        "tasks": sum(1 for n in notes if n.signifier is Signifier.TASK),
        "tags": len({tag for n in notes for tag in n.tags}),
        # From the caller when it has the real list, because `/api/collections`
        # counts an empty templates folder and the notes cannot: a card
        # disagreeing with the view it links to is worse than no card.
        "collections": len(collections) if collections is not None
        else len({n.collection for n in notes if n.collection}),
    }


def calendar_card(counts: Mapping[str, int], *, month: date, today: date) -> dict:
    """One month, with how much is written on each day.

    The grid itself is built in the browser by `monthGrid` in
    `static/js/calendar.js` -- the same function the Calendar view uses, so the
    two cannot lay a month out differently. This sends the month to lay out and
    the counts, and nothing about where the cells go: which weekday the 1st
    falls on is the grid's business, and sending it as well would be a second
    answer waiting to be wrong.
    """
    first = month.replace(day=1)
    prefix = f"{first.year:04d}-{first.month:02d}-"
    in_month = (today.year, today.month) == (first.year, first.month)
    return {
        "year": first.year,
        "month": first.month,
        "label": f"{calendar.month_name[first.month]} {first.year}",
        "today": today.isoformat() if in_month else None,
        "counts": {d: c for d, c in counts.items() if d.startswith(prefix)},
    }


def today_card(notes: Sequence[Note], today: date) -> dict:
    """Today: whether there is a note yet, and what has been finished.

    The finished list is `daily.completed_on` -- the same call the day's own note
    and every `completed` query make, so the count here is the count you get by
    opening the day.
    """
    note = next((n for n in notes if n.id == daily.daily_note_id(today)), None)
    finished = daily.completed_on(notes, today)
    return {
        "date": today.isoformat(),
        "has_note": note is not None,
        "summarised": bool(note is not None and daily.has_summary(note.body)),
        "finished": len(finished),
        "titles": [n.title for n in finished],
    }


def mood_card(notes: Sequence[Note], today: date) -> dict:
    """The mood streak and today's reading, from the mood layer's own summary."""
    start = today - timedelta(days=MOOD_WINDOW_DAYS - 1)
    series = moodlib.daily_series(notes, start=start, end=today)
    summary = moodlib.summarize(series, today=today)
    reading = next((d for d in series if d.get("date") == today.isoformat()), {})
    return {
        "streak": summary.get("streak", 0),
        "days_logged": summary.get("days_logged", 0),
        "logged_today": bool(summary.get("logged_today")),
        "level": reading.get("mood"),
        "pain": reading.get("pain"),
        "most_common": summary.get("most_common"),
    }


def summary(
    notes: Sequence[Note],
    *,
    root: Path | str,
    today: date,
    calendar_counts: Mapping[str, int],
    month: Optional[date] = None,
    collections: Optional[Sequence[str]] = None,
) -> dict:
    """Everything the dashboard shows. One call, so the cards agree."""
    # Filtered the way the board view filters before it counts, so the card and
    # the view it links to describe the same set of things.
    workable = [n for n in notes if is_work(n)]
    board = board_summary(workable, index_by_id(workable))
    return {
        "vault": vault_name(root),
        "today": today.isoformat(),
        "calendar": calendar_card(calendar_counts, month=month or today, today=today),
        "statistics": statistics(notes, collections=collections),
        "board": board,
        "today_card": today_card(notes, today),
        "mood": mood_card(notes, today),
    }
