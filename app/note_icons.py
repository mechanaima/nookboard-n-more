"""A note's icon: the name it carries, and what to say when that name draws nothing.

The drawings and the names are generated (`app/icons.py`, `static/vendor/lucide.js`,
both from `tools/build-lucide.py`). This is the hand-written part -- the two rules
the rest of the app asks about, in one place so the API, the board and the bookmarks
view cannot each have their own opinion:

**Whitespace is not a name.** A cleared icon field saved as `" "` is empty, the same
way a cleared address is.

**An unknown name is kept and reported, not refused.** A note is the person's own
file: if they type `icon: serverr`, the note keeps it, the API says it is not a
Lucide icon, and the view draws the fallback. Losing their line over a typo is the
worse failure -- the same call this app makes about an address a browser cannot open.
"""

from __future__ import annotations

from .icons import LUCIDE_VERSION, NAME_SET

__all__ = ["LUCIDE_VERSION", "clean", "icon_problem", "is_known"]


def clean(name: str | None) -> str | None:
    """The name as written, or None. Whitespace is not a name."""
    text = (name or "").strip()
    return text or None


def is_known(name: str | None) -> bool:
    """Whether this names something Lucide can draw."""
    return clean(name) is not None and clean(name) in NAME_SET


def icon_problem(name: str | None) -> str | None:
    """Why nothing can be drawn for this, in a sentence, or None if it can.

    Empty is not a problem -- it is a note without an icon, which is most of them.
    """
    text = clean(name)
    if text is None or text in NAME_SET:
        return None
    return f"\u201c{text}\u201d is not a Lucide icon, so nothing can be drawn for it"
