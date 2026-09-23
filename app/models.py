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
        return frontmatter.dumps(post)

    @classmethod
    def from_markdown(cls, md: str) -> "Note":
        post = frontmatter.loads(md)
        meta = post.metadata
        return cls(
            id=meta["id"],
            collection=meta.get("collection", "inbox"),
            title=meta.get("title", "Untitled"),
            body=post.content,
            signifier=Signifier(meta.get("signifier", "note")),
            status=Status(meta.get("status", "open")),
            dates=[date.fromisoformat(d) for d in meta.get("dates", [])],
            parent_id=meta.get("parent_id"),
            created=date.fromisoformat(meta["created"]) if "created" in meta else date.today(),
            mood=meta.get("mood"),
            tags=list(meta.get("tags", []) or []),
            recurrence=meta.get("recurrence"),
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signifier"] = self.signifier.value
        d["status"] = self.status.value
        d["dates"] = [x.isoformat() for x in self.dates]
        d["created"] = self.created.isoformat()
        return d