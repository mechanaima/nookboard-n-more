"""Templates: a note that is a shape for other notes.

A template is an ordinary note living in the `templates/` collection. No new
storage, no new file format -- you write one in the app like anything else, and
you can see it in the vault with plain `cat`. What this module adds is the small
vocabulary a template needs to say what the note it becomes should look like.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable, Optional

# `TEMPLATES_COLLECTION` and `is_template` are re-exported from `models` because
# they stopped being only this module's business: the board, the dashboard's
# counts and an unscoped query all have to agree about what a shape is. A folder,
# like every other collection, so there is one rule: put a note in the folder and
# it becomes a template.
from .models import (
    TEMPLATES_COLLECTION,
    Note,
    Signifier,
    Status,
    is_template,
    iso_week,
    iso_week_bounds,
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: What a template is allowed to say. Six things, and no format mini-language:
#: `{{date}}` is always ISO. A template that renders differently from one day to
#: the next is a template you cannot rely on.
#:
#: `week_start`/`week_end` exist because a template about a week has to say
#: which week, and `{{week}}` alone is a label (`2026-W39`) rather than a span.
KNOWN = ("date", "time", "week", "title", "week_start", "week_end")


def list_templates(notes: Iterable[Note]) -> list[Note]:
    """The templates, in a stable order so the list does not jump around."""
    found = [n for n in notes if is_template(n)]
    return sorted(found, key=lambda n: (n.title.lower(), n.id))


def expand(
    text: str,
    *,
    day: date,
    now: Optional[datetime] = None,
    title: Optional[str] = None,
) -> str:
    """Replace the placeholders a template may use.

    Anything not in `KNOWN` is left exactly as written. A template is a document
    you wrote, so quietly eating `{{stuff}}` -- or worse, guessing at it -- would
    change what your notes say. Leaving it visible means you see the mistake
    instead of inheriting it into a hundred notes.

    `{{title}}` only resolves when there is a title to resolve it to, which is
    why a template's *body* gets one and its title field does not: at the moment
    the title is being worked out, it does not exist yet.
    """
    now = now or datetime.now()

    def one(match: re.Match) -> str:
        name = match.group(1).lower()
        if name == "date":
            return day.isoformat()
        if name == "time":
            return now.strftime("%H:%M")
        if name == "week":
            return iso_week(day)
        if name in ("week_start", "week_end"):
            monday, sunday = iso_week_bounds(day)
            return (monday if name == "week_start" else sunday).isoformat()
        if name == "title" and title is not None:
            return title
        return match.group(0)

    return PLACEHOLDER_RE.sub(one, text or "")


def unique_title(title: str, taken: Iterable[str]) -> str:
    """A title not already in use, by appending `(2)`, `(3)`, ...

    Without this, applying the same template twice makes two notes with the same
    title, and this vault resolves `[[wikilinks]]` by title -- so the pair
    resolve to each other and the backlinks stop meaning anything.
    """
    title = (title or "").strip()
    used = {t.strip().lower() for t in taken if t}
    if title.lower() not in used:
        return title
    n = 2
    while f"{title} ({n})".lower() in used:
        n += 1
    return f"{title} ({n})"


def build_note(
    template: Note,
    *,
    note_id: str,
    title: Optional[str] = None,
    collection: str = "inbox",
    day: Optional[date] = None,
    now: Optional[datetime] = None,
    taken: Iterable[str] = (),
) -> Note:
    """The note a template becomes.

    The template's signifier and tags carry over, so a template that is a task
    makes a task -- that is what makes a task template worth having. Its state
    does not: a new note starts open, whatever the template was doing, and a
    completion date is never inherited.
    """
    day = day or date.today()
    now = now or datetime.now()

    wanted = (title or "").strip() or expand(template.title, day=day, now=now).strip()
    if not wanted:
        wanted = f"Untitled {day.isoformat()}"
    final_title = unique_title(wanted, taken)

    return Note(
        id=note_id,
        collection=collection or "inbox",
        title=final_title,
        body=expand(template.body, day=day, now=now, title=final_title),
        signifier=template.signifier,
        status=Status.OPEN,
        # Dated the day it is made, like a note captured any other way, so it
        # shows up on that day rather than nowhere.
        dates=[day],
        tags=list(template.tags),
        created=day,
    )
