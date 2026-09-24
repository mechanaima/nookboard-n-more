"""Mood and pain over time — pure functions over notes, no I/O.

Every reading lives on a *note*, so a "day" here is a date that some note
belongs to, not a row in its own table. That means two questions have to be
answered deliberately, and both are answered here rather than in the UI:

1. **Which day is a note's reading for?** A note carrying explicit dates
   belongs to each of them — that is what dating a note means, and it is how
   you backfill a week you did not write up. A note with no dates belongs to
   the day it was captured.

2. **What does a day with several readings show?** One cell can only hold one
   value, so a day collapses to its *worst* mood and its *highest* pain. This
   is the one place averaging would be actively harmful: a good morning and a
   bad evening average into a flat "meh", which hides exactly the day you would
   want to look back at. The individual readings stay available on the day, so
   nothing is lost — only the summary is pessimistic.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from math import floor
from typing import Iterable, Optional

from .models import MOOD_LEVELS, MOOD_SCORE, Note, normalize_mood

#: Reverse of MOOD_SCORE. Unique because every level has a distinct score.
_LEVEL_FOR_SCORE: dict[int, str] = {score: level for level, score in MOOD_SCORE.items()}


def note_days(note: Note) -> list[date]:
    """The days a note's reading counts for.

    Explicit dates win; an undated note counts for the day it was captured.
    Duplicates are removed but order is kept, so a note dated twice on the
    same day does not double-count.
    """
    if note.dates:
        return list(dict.fromkeys(note.dates))
    return [note.created]


def readings(notes: Iterable[Note]) -> dict[date, list[dict]]:
    """Bucket every mood/pain reading by day.

    Notes carrying neither a mood nor a pain reading are skipped entirely —
    a day is only "logged" if something was actually recorded on it, otherwise
    every note ever written would look like a mood entry.
    """
    buckets: dict[date, list[dict]] = defaultdict(list)
    for note in notes:
        mood = normalize_mood(note.mood)
        pain = note.pain
        if mood is None and pain is None:
            continue
        entry = {
            "id": note.id,
            "title": note.title,
            "mood": mood,
            "score": MOOD_SCORE[mood] if mood else None,
            "pain": pain,
        }
        for day in note_days(note):
            buckets[day].append(entry)
    return dict(buckets)


def collapse(entries: list[dict]) -> dict:
    """Reduce one day's readings to what a single cell can show."""
    scored = [e["score"] for e in entries if e["score"] is not None]
    pains = [e["pain"] for e in entries if e["pain"] is not None]
    worst = min(scored) if scored else None
    return {
        "mood": _LEVEL_FOR_SCORE[worst] if worst is not None else None,
        "score": worst,
        "pain": max(pains) if pains else None,
        "count": len(entries),
        # Distinct levels, best -> worst, so the UI can flag a mixed day.
        "moods": [lvl for lvl in MOOD_LEVELS if any(e["mood"] == lvl for e in entries)],
    }


def daily_series(
    notes: Iterable[Note],
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> list[dict]:
    """One collapsed record per logged day, oldest first.

    Only days that have a reading are returned: gaps are the caller's to draw,
    and an empty day is indistinguishable from one the range simply excludes.
    """
    days = []
    for day, entries in readings(notes).items():
        if start and day < start:
            continue
        if end and day > end:
            continue
        record = {"date": day.isoformat()}
        record.update(collapse(entries))
        record["notes"] = sorted(entries, key=lambda e: (e["title"] or "").lower())
        days.append(record)
    days.sort(key=lambda d: d["date"])
    return days


def streak(days: list[dict], today: date) -> int:
    """Consecutive logged days ending today.

    Deliberately forgiving about *today*: a streak is not broken until the day
    is over, so an unlogged today continues the run from yesterday instead of
    reporting zero and making you feel like you missed one.
    """
    logged = {d["date"] for d in days if d["mood"] is not None}
    if not logged:
        return 0
    cursor = today
    if cursor.isoformat() not in logged:
        cursor -= timedelta(days=1)
        if cursor.isoformat() not in logged:
            return 0
    count = 0
    while cursor.isoformat() in logged:
        count += 1
        cursor -= timedelta(days=1)
    return count


def summarize(days: list[dict], today: Optional[date] = None) -> dict:
    """Headline numbers for a series."""
    today = today or date.today()
    logged = [d for d in days if d["mood"] is not None]
    scores = [d["score"] for d in logged]
    pains = [d["pain"] for d in days if d["pain"] is not None]

    counts = {level: 0 for level in MOOD_LEVELS}
    for day in logged:
        counts[day["mood"]] += 1

    most_common = None
    if logged:
        # Ties go to the better mood: "most common" is a neutral label, and
        # picking the worst on a tie would read as a verdict. Sorting ascending
        # means negating the score too, so the better level comes first.
        most_common = sorted(
            MOOD_LEVELS, key=lambda lvl: (-counts[lvl], -MOOD_SCORE[lvl])
        )[0]

    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    avg_pain = round(sum(pains) / len(pains), 1) if pains else None

    return {
        "days_logged": len(logged),
        "entries": sum(d["count"] for d in days),
        "streak": streak(days, today),
        "logged_today": any(d["date"] == today.isoformat() and d["mood"] for d in days),
        "counts": counts,
        "most_common": most_common,
        "avg_score": avg_score,
        # The level nearest the average, so the UI can name it in words.
        # `floor(x + 0.5)` rather than round(): round() is banker's rounding,
        # so 2.5 would come out as 2 and 3.5 as 4 — inconsistent in a way
        # nobody expects from an average.
        "avg_mood": _LEVEL_FOR_SCORE[min(5, max(1, floor(avg_score + 0.5)))] if avg_score else None,
        "avg_pain": avg_pain,
        "pain_days": len(pains),
        "first": days[0]["date"] if days else None,
        "last": days[-1]["date"] if days else None,
    }
