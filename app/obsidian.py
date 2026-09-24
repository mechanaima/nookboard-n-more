"""Obsidian interoperability helpers.

nookboard and Obsidian agree on the storage format (plain Markdown, YAML
frontmatter) but disagree on some conventions. This module holds the
translation so the rest of the app does not have to care.

Differences handled here:

  [[link]] resolution
      Obsidian resolves a wikilink by filename or by an entry in the note's
      `aliases:`. nookboard stores files as `<id>.md` and resolves by the
      `title:` field. We emit `aliases:` on write so both resolve, and we
      strip the `|display` half on read so backlinks match by target.

  inline tags
      Obsidian supports `#tag` in the body as well as `tags:` in frontmatter.
      Both are merged into one list.

  absence of frontmatter
      Any `.md` file in an Obsidian vault may have no frontmatter at all.
      Parsing has to tolerate that rather than raising.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
INLINE_TAG_RE = re.compile(r"(?<![\w/#])#([A-Za-z][A-Za-z0-9_\-/]*)")
FENCED_CODE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
INLINE_CODE_RE = re.compile(r"`[^`]*`")


def strip_code(md: str) -> str:
    """Blank out fenced blocks and inline spans so we don't scan code."""
    text = FENCED_CODE_RE.sub(" ", md or "")
    return INLINE_CODE_RE.sub(" ", text)


def extract_inline_tags(md: str) -> list[str]:
    """Body `#tags`, deduped, order preserved.

    Deliberately does not match `# Heading` (space after the hash) or
    `url#fragment` (hash preceded by a word character) or `#123`
    (Obsidian requires a leading letter).
    """
    out: list[str] = []
    for m in INLINE_TAG_RE.finditer(strip_code(md or "")):
        tag = m.group(1).rstrip("/")
        if tag and tag not in out:
            out.append(tag)
    return out


def wikilink_targets(md: str) -> list[str]:
    """Link targets, deduped, order preserved. Handles `[[Target|Display]]`."""
    out: list[str] = []
    for m in WIKILINK_RE.finditer(strip_code(md or "")):
        target = m.group(1).split("|", 1)[0].strip()
        if target and target not in out:
            out.append(target)
    return out


def split_frontmatter_tags(value: Any) -> list[str]:
    """Normalise every shape Obsidian accepts for `tags:`.

    Obsidian allows a YAML list (`[a, b]`), a space-separated string
    (`"a b"`), and entries optionally written with a leading `#`.
    """
    if value is None:
        return []
    if isinstance(value, str):
        raw: Iterable[str] = re.split(r"[,\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw = []
        for item in value:
            raw.extend(str(item).split(","))
    else:
        raw = [str(value)]
    out: list[str] = []
    for item in raw:
        tag = str(item).strip().lstrip("#").strip()
        if tag and tag not in out:
            out.append(tag)
    return out
