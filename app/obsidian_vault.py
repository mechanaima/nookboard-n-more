"""Read a secondary Obsidian vault for the dashboard's recent-notes card.

This never writes to the vault — it is a read-only mirror of whatever the user
points us at (e.g. ~/Documents/School).  NOOKBOARD_OBSIDIAN_VAULT controls it;
if empty the feature is disabled and the API returns null.

Obsidian deep-links use the scheme:
    obsidian://open?vault=<vault-name>&file=<path>

<vault-name> is the folder name of the vault.  <path> is the file path
relative to the vault root, without the .md extension, with / separators and
each component URL-encoded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


MD_EXT_RE = re.compile(r"\.md$", re.IGNORECASE)
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
TITLE_RE = re.compile(r"^title:\s*(.+)$", re.MULTILINE)
EXCERPT_STRIP_RE = re.compile(
    r"\[\[([^\]]+?)\|([^\]]*?)\]\]"  # [[link|display]] → display
    r"|\[\[([^\]]+?)\]\]"            # [[link]] → link
    r"|!\[([^\]]*?)\]\([^\)]*\)"      # ![alt](url) → alt or empty
    r"|\[([^\]]+?)\]\([^\)]*\)"        # [text](url) → text
    r"|<[^>]+>"                        # HTML tags
    r"|[#*`_~>|+-]+"                  # markdown syntax chars
    r"|\s{2,}",                        # 2+ whitespace
    re.DOTALL,
)


@dataclass
class ObsidianNote:
    """One note from a secondary Obsidian vault."""

    id: str  # vault-relative path without .md (safe for URL component)
    title: str  # display name
    excerpt: str  # ~200 chars of plain-text content
    obsidian_url: str  # obsidian:// open URL
    mtime: float  # filesystem mtime, for sorting


@dataclass
class ObsidianVault:
    """Read-only view of a secondary Obsidian vault."""

    root: Path

    @classmethod
    def from_path(cls, path: Path | str) -> ObsidianVault:
        return cls(root=Path(path).expanduser().resolve())

    @classmethod
    def disabled(cls) -> ObsidianVault:
        """Sentinel that produces no notes."""
        return cls(root=Path("/nonexistent"))

    def is_enabled(self) -> bool:
        return self.root.exists() and self.root.is_dir()

    def recent_notes(self, limit: int = 8) -> list[ObsidianNote]:
        """Return the <limit> most recently modified .md files."""
        if not self.is_enabled():
            return []
        notes: list[ObsidianNote] = []
        for md in self._iter_md():
            try:
                mtime = md.stat().st_mtime
            except OSError:
                continue
            rel = md.relative_to(self.root)
            note_id = rel.with_suffix("").as_posix()  # strip .md, use / separators
            title = self._title_of(md)
            excerpt = self._excerpt_of(md)
            obsidian_url = self._obsidian_url(note_id)
            notes.append(
                ObsidianNote(
                    id=note_id,
                    title=title,
                    excerpt=excerpt,
                    obsidian_url=obsidian_url,
                    mtime=mtime,
                )
            )
        notes.sort(key=lambda n: n.mtime, reverse=True)
        return notes[:limit]

    def _iter_md(self) -> Iterator[Path]:
        """Walk the vault, skipping dot-folders and dot-files."""
        for item in self.root.rglob("*.md"):
            # Skip dot-folders: any vault-relative directory component starting with dot.
            # Only the FILENAME's .md extension is excluded; real dot-folders (.obsidian,
            # .trash) are always excluded.
            rel_parts = item.relative_to(self.root).parts[:-1]  # directories only
            if any(p.startswith(".") for p in rel_parts):
                continue
            yield item

    def _title_of(self, md: Path) -> str:
        """Prefer frontmatter title, else the filename without extension."""
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return md.stem
        m = FRONTMATTER_RE.match(text)
        if m:
            t = TITLE_RE.search(m.group(1))
            if t:
                return t.group(1).strip()
        return md.stem

    def _excerpt_of(self, md: Path, length: int = 220) -> str:
        """Return plain-text excerpt from the note body, skipping frontmatter."""
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        # Strip frontmatter: remove the whole ---...--- block at the start
        m = FRONTMATTER_RE.match(text)
        body = text[m.end() :] if m else text
        # Remove code blocks first (they may contain weird chars)
        body = re.sub(r"```[\s\S]*?```", "", body)
        body = re.sub(r"`[^`]*`", "", body)
        # Clean markdown syntax
        body = EXCERPT_STRIP_RE.sub(" ", body)
        body = re.sub(r"\s+", " ", body).strip()
        return body[:length].rsplit(" ", 1)[0] + "…" if len(body) > length else body

    def _obsidian_url(self, note_id: str) -> str:
        vault_name = self.root.name
        # Each path component must be URL-encoded; / is the component separator
        encoded = "/".join(_url_encode(p) for p in note_id.split("/"))
        return f"obsidian://open?vault={_url_encode(vault_name)}&file={encoded}"


def _url_encode(s: str) -> str:
    """Percent-encode a single path component."""
    return (
        s.encode("utf-8")
        .decode("utf-8")
        .replace("%", "%25")
        .replace("/", "%2F")
        .replace("\\", "%5C")
        .replace(":", "%3A")
        .replace("#", "%23")
        .replace("?", "%3F")
    )
