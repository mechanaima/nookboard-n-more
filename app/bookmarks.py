"""Bookmarks: a note that points somewhere else.

A bookmark is a note with a `url:` -- the same shape as a workspace (a note with a
`path:`), and for the same reason. The note holds what *you* want to say about the
link; the vault stays a folder of markdown you already back up and can grep.

This exists because the addresses of your own machines are exactly the kind of thing
that ends up in a browser's bookmarks bar, which is the one place you cannot read
from a file. "Services and the like" is a list, and a list belongs in the vault.

Nothing here opens anything. The view renders an anchor and the browser does the
rest, which is why this module needs no subprocess and no network -- it is a
grouping and a check, nothing more.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from .models import Note

#: Only these can be a link. A note is the person's own file and may say anything, so
#: an unusable url is *reported* rather than refused when it is saved -- losing the
#: note over a typo would be the worse failure.
SCHEMES = ("http", "https")

#: Where a bookmark goes when it has no tag of its own.
OTHER = "Other"

#: The group that is not a group: things that cannot be opened. Kept separate and
#: shown, because a link that silently does nothing reads as the app being broken.
UNUSABLE = "Cannot be opened"


def url_of(note: Note) -> str | None:
    """The address as written, or None. Whitespace is not an address."""
    text = (note.url or "").strip()
    return text or None


def is_bookmark(note: Note) -> bool:
    """A note is a bookmark iff it has a url. One rule, no marker tag."""
    return url_of(note) is not None


def url_problem(url: str) -> str | None:
    """Why a browser could not open this, in a sentence, or None if it can.

    A sentence rather than a code: the person reading it is the person who typed it,
    and "no scheme" is only useful to someone who already knows what a scheme is.
    """
    parts = urlsplit(url)
    if not parts.scheme:
        return "this has no \u201chttps://\u201d in front, so it does not name an address"
    if parts.scheme.lower() not in SCHEMES:
        return f"\u201c{parts.scheme}:\u201d is not something a browser opens from a link"
    if not parts.netloc:
        return "there is no host in this"
    return None


def host_of(url: str) -> str:
    """What a link goes to, for showing: the host and, when it has one, the port.

    `netloc` is the whole authority (`127.0.0.1:8796`, `braxia.tel`), which is exactly
    the part that tells two bookmarks apart when their titles are similar.
    """
    return urlsplit(url).netloc


def group_of(note: Note) -> str:
    """A bookmark's group is its first tag, spelled the way the person spelled it."""
    return note.tags[0] if note.tags else OTHER


def _group_key(entry: tuple[str, list[dict]]) -> tuple[bool, str]:
    """Tags alphabetically, and `Other` last: it is the absence of a decision, not one."""
    name = entry[0]
    return (name == OTHER, name.casefold())


def _item(note: Note, url: str) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "url": url,
        "host": host_of(url),
        "problem": url_problem(url),
        "tags": list(note.tags),
        # A bookmark's body is usually the reason it is in the list at all.
        "body": note.body.strip(),
    }


def view(notes: list[Note]) -> dict:
    """The whole answer the Bookmarks view needs, assembled in one place.

    Grouped and ordered here rather than in the browser, for the reason every other
    list in this app is: two places that sort are two orders that can disagree, and
    "the groups are what the tags say" is a rule, not a rendering choice.
    """
    grouped: dict[str, list[dict]] = {}
    unusable: list[dict] = []

    for note in notes:
        url = url_of(note)
        if url is None:
            continue
        item = _item(note, url)
        if item["problem"]:
            unusable.append(item)
            continue
        grouped.setdefault(group_of(note), []).append(item)

    groups = [
        {"name": name, "items": sorted(items, key=lambda i: i["title"].casefold())}
        for name, items in sorted(grouped.items(), key=_group_key)
    ]
    if unusable:
        groups.append(
            {
                "name": UNUSABLE,
                "problem": True,
                "items": sorted(unusable, key=lambda i: i["title"].casefold()),
            }
        )

    return {
        "groups": groups,
        "count": sum(len(g["items"]) for g in groups),
        "unusable": len(unusable),
        "other": OTHER,
    }


def summary_line(payload: dict) -> str:
    """The one line above the list. Words, so the count and its noise read as one fact."""
    count = payload["count"] - payload["unusable"]
    if not count and not payload["unusable"]:
        return "nothing bookmarked yet"
    parts = [f"{count} bookmark" + ("" if count == 1 else "s")]
    if payload["unusable"]:
        parts.append(
            f"{payload['unusable']} that cannot be opened"
            if payload["unusable"] != 1
            else "1 that cannot be opened"
        )
    return " \u00b7 ".join(parts)
