"""Keeping a vault's past, said in words rather than in git’s.

A note that can be edited but not un-edited is a note you handle carefully. The
vault is a folder of ordinary files, which is the whole point of this app — and a
folder of ordinary files has one obviously good way to keep its past: git, in the
vault, invisible.

This module is what history *means*. It has no I/O and runs nothing; the git
commands live in `history_run.py`. So the phrasing of a commit message, the shape
of a log entry, and the question "is this path one I am allowed to touch" are all
testable without a repository on disk.

Three rules this layer exists to keep straight:

- **The app writes the commits, and says it did.** Every commit it makes is
  authored by the app, not by the person: a history that claimed someone wrote
  something they did not is worse than no history.
- **A restore writes one file.** Undo here is not `git reset` — it is reading an
  old version of one note and saving it, which is the same act as editing it by
  hand and leaves the rest of the vault alone.
- **A path from a request is a path from a request.** Even inside the vault,
  `../../etc/passwd` is refused rather than resolved.
"""

from __future__ import annotations

import os

from . import workspace

#: The app is the author of the commits it makes. A note's history should not
#: claim the person wrote a line they did not write.
COMMIT_NAME = "nookboard"
COMMIT_EMAIL = "nookboard@localhost"

#: What a commit message calls each act. Small, and deliberately not "update":
#: a history you cannot skim is a history you do not read.
MESSAGE_VERBS = {
    "new": "new",
    "edit": "edit",
    "delete": "delete",
    "restore": "restore",
}

#: Written *into the vault* the first time history is turned on. The index is a
#: cache of the markdown that is already there (and `POST /api/rebuild-index`
#: rebuilds it), so committing it would version a derived file beside its source.
VAULT_GITIGNORE = """\
# Written by nookboard when history was turned on.
#
# The index is built from the markdown beside it and can be rebuilt at any time
# (POST /api/rebuild-index), so it is not history -- it is a cache of history.
*.sqlite
*.sqlite-journal
"""

#: Field and record separators for `git log --format`. Not `|` and not a newline:
#: a commit subject is arbitrary text and can contain either.
FIELD_SEP = "\x1f"
RECORD_SEP = "\x1e"


def safe_relpath(root: str, relpath) -> str | None:
    """The vault-relative path a request may write to, or `None`.

    The sibling of `workspace.inside_folder`, and different on purpose: this one
    does *not* require the file to exist, because the thing being restored is
    often a note that was deleted — which is exactly when you want its history.

    Absolute paths and `..` are refused, not normalized away: `os.path.join`
    throws away everything before an absolute second argument, so `join` followed
    by a check is the only safe order, and a path that leaves the vault is not a
    path this app will write.
    """
    if not isinstance(relpath, str):
        return None
    candidate = relpath.strip()
    if not candidate or candidate.startswith(("-", "/")):
        return None
    root = os.path.realpath(root)
    joined = os.path.realpath(os.path.join(root, candidate))
    if not joined.startswith(root + os.sep):
        return None
    return os.path.relpath(joined, root)


def commit_message(act: str, title: str) -> str:
    """What one change is called in the log.

    The title is the note's, trimmed and cut: a commit subject that runs to 300
    characters of somebody's prose is unreadable in a log, and the first line is
    all `git log --oneline` shows anyway.
    """
    verb = MESSAGE_VERBS.get(act) or "edit"
    cleaned = " ".join((title or "").split()) or "untitled"
    if len(cleaned) > 72:
        cleaned = cleaned[:71].rstrip() + "…"
    return f"{verb}: {cleaned}"


def parse_log(text: str) -> list[dict]:
    """A `git log` in the format above, as a list of entries.

    A commit is one record; the fields are separated by characters that cannot
    appear in a subject, so a note titled "a | b" is not two fields.
    """
    entries = []
    for record in text.split(RECORD_SEP):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split(FIELD_SEP)
        if len(parts) < 4:
            continue
        sha, short, when, subject = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3]
        entries.append({
            "sha": sha,
            "short": short,
            "when": when,
            "subject": subject.strip("\n"),
        })
    return entries


def log_format() -> str:
    """The `--format` that `parse_log` reads. One place, so the two cannot drift."""
    return FIELD_SEP.join(["%H", "%h", "%aI", "%s"]) + RECORD_SEP


def delete_format() -> str:
    """The `--format` that `parse_deletions` reads, for `--name-only` output.

    Carries `%P` -- the parents -- because the version worth restoring a deleted
    note *from* is the deletion's parent, and a parent is a real sha. Recording
    `"<sha>^"` instead would mean the app resolving revision syntax, which it
    does not do.
    """
    return "%x1e" + FIELD_SEP.join(["%H", "%h", "%P", "%aI", "%s"])


def summary_line(changes: list[dict], deleted: list[dict], now, pending: int = 0) -> str:
    """The sentence the history view leads with.

    Written here rather than in the browser, for the same reason the rest of this
    app computes its counts server-side: a client deriving its own sentence can
    disagree with the list underneath it, and a heading that disagrees with its
    own list is worse than no heading.
    """
    if not changes:
        if pending:
            return f"{pending} file{'s' if pending != 1 else ''} not recorded yet."
        return "Nothing recorded yet."
    count = len(changes)
    line = f"{count} change{'s' if count != 1 else ''} recorded, the last {version_words(changes[0], now)}"
    if pending:
        # The actionable half of the sentence, so it goes before the sad half.
        line += f" · {pending} file{'s' if pending != 1 else ''} not recorded yet"
    if deleted:
        line += f" · {len(deleted)} note{'s' if len(deleted) != 1 else ''} to bring back"
    return line


def parse_deletions(text: str) -> list[dict]:
    """The paths each deletion commit removed, and the version to bring them back.

    `git log --diff-filter=D --name-only` prints a commit's header and then the
    paths it removed, which is a shape worth parsing in one place rather than
    twice. A path whose deletion commit has no parent is skipped: there is no
    earlier version of it, so there is nothing to offer.
    """
    found: list[dict] = []
    for record in text.split(RECORD_SEP):
        lines = record.strip("\n").split("\n")
        header = lines[0].split(FIELD_SEP) if lines else []
        if len(header) < 5:
            continue
        sha, short, parents, when, subject = (part.strip() for part in header[:5])
        parent = parents.split()[0] if parents else ""
        if not parent:
            continue
        for path in lines[1:]:
            path = path.strip()
            if not path:
                continue
            found.append({
                "sha": sha,
                "short": short,
                "when": when,
                "subject": subject,
                "path": path,
                # The deletion holds no text; the version before it does.
                "restore_from": parent,
            })
    return found


def clock_label(when: str) -> str:
    """The time of a change as a clock reading, in the change's own offset.

    A list of ten rows that all say "just now" tells you nothing: the clock is
    what separates them. Sent *alongside* the words rather than instead of them,
    and the client picks -- today shows the clock, older shows the distance --
    which is also why this is not converted: the time the commit recorded is the
    time it happened.
    """
    moment = workspace.parse_moment(when)
    return moment.strftime("%H:%M") if moment else ""


def version_words(entry: dict, now) -> str:
    """When a version was written, in the words a person would use.

    `relative_age` answers "never" for a moment it cannot read, which is true of
    a *folder* with no commit and false of a *version*: this entry is right here
    in the log. So an unreadable date says only what is true -- that it happened
    at some point -- rather than borrowing a word that means something else.
    """
    when = entry.get("when") or ""
    if not when or workspace.parse_moment(when) is None:
        return "at some point"
    return workspace.relative_age(when, now) or "at some point"
