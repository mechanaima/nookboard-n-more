"""Domain model for nookboard notes.

A Note is the universal record. It can be:
  - a long-form NeatNook-style entry inside a collection, or
  - a date-tagged Agenda-style note that surfaces on the timeline, or
  - a rapid-log BuJo-style bullet (task / event / note).

The same record drives all three because they share the same primitives
in practice: a thing you wrote, with optional dates, optional collection,
and optional task state.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Optional

import frontmatter

from .obsidian import extract_inline_tags, split_frontmatter_tags


class Signifier(str, Enum):
    """BuJo bullet signifiers. None = general note, not a bullet."""
    TASK = "task"          # •  (to-do)
    EVENT = "event"        # ○  (date-related)
    NOTE = "note"          # –  (observation / fact)


class Status(str, Enum):
    """BuJo task states. Ignored for Signifier.NOTE."""
    OPEN = "open"          # •
    COMPLETE = "complete"  # X
    MIGRATED = "migrated"  # >
    SCHEDULED = "scheduled"# <
    IRRELEVANT = "irrelevant"  # struck through


def _coerce(enum_cls, value, default):
    """Enum lookup that falls back instead of raising.

    Foreign vaults may carry values we do not know (or none at all); a note
    we cannot classify is better than a note we refuse to open.
    """
    if value is None:
        return default
    try:
        return enum_cls(str(value))
    except ValueError:
        return default


@dataclass(frozen=True)
class Note:
    id: str
    collection: str
    title: str
    body: str
    signifier: Signifier = Signifier.NOTE
    status: Status = Status.OPEN
    dates: list[date] = field(default_factory=list)
    parent_id: Optional[str] = None
    created: date = field(default_factory=date.today)
    mood: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    recurrence: Optional[str] = None  # "daily" | "weekly" | "monthly"
    # Where this note lives in the vault, relative to the vault root. Set by
    # Vault on read so writes return to the same file. Never persisted to
    # frontmatter, and excluded from equality so round-trip tests are unaffected.
    source_rel: Optional[str] = field(default=None, compare=False, repr=False)

    def to_markdown(self) -> str:
        post = frontmatter.Post(self.body)
        post.metadata = {
            "id": self.id,
            "collection": self.collection,
            "title": self.title,
            "signifier": self.signifier.value,
            "status": self.status.value,
            "dates": [d.isoformat() for d in self.dates],
            "parent_id": self.parent_id,
            "created": self.created.isoformat(),
            "mood": self.mood,
            "tags": list(self.tags),
            "recurrence": self.recurrence,
        }
        # Obsidian resolves [[Title]] by filename or alias, never by our
        # `title:` field, so record the title as an alias to make the same
        # wikilink work in both apps.
        if self.title:
            post.metadata["aliases"] = [self.title]
        return frontmatter.dumps(post)

    @classmethod
    def from_markdown(cls, md: str, *, fallback_id: str | None = None) -> "Note":
        """Parse a note. Tolerates arbitrary Obsidian files.

        A file in someone else's vault may have no frontmatter, or frontmatter
        missing any field we care about. `fallback_id` (normally the filename
        stem) supplies id and title when the frontmatter does not.
        """
        post = frontmatter.loads(md)
        meta = dict(post.metadata or {})
        body = post.content or ""
        stem = str(meta.get("id") or fallback_id or "untitled")

        fm_tags = split_frontmatter_tags(meta.get("tags"))
        tags = list(fm_tags)
        for tag in extract_inline_tags(body):
            if tag not in tags:
                tags.append(tag)

        dates: list[date] = []
        raw_dates = meta.get("dates") or []
        if isinstance(raw_dates, str):
            raw_dates = [raw_dates]
        for raw in raw_dates:
            try:
                dates.append(date.fromisoformat(str(raw)))
            except ValueError:
                continue

        try:
            created = date.fromisoformat(str(meta["created"])) if meta.get("created") else date.today()
        except ValueError:
            created = date.today()

        return cls(
            id=stem,
            collection=str(meta.get("collection") or "inbox"),
            title=str(meta.get("title") or stem),
            body=body,
            signifier=_coerce(Signifier, meta.get("signifier"), Signifier.NOTE),
            status=_coerce(Status, meta.get("status"), Status.OPEN),
            dates=dates,
            parent_id=meta.get("parent_id"),
            created=created,
            mood=meta.get("mood"),
            tags=tags,
            recurrence=meta.get("recurrence"),
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signifier"] = self.signifier.value
        d["status"] = self.status.value
        d["dates"] = [x.isoformat() for x in self.dates]
        d["created"] = self.created.isoformat()
        return d