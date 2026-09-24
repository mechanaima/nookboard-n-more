"""The part of history that runs git, and it runs it in the vault.

Deliberately the smallest surface that can do the job, for the same reason
`workspace_run.py` is: every call here is a process, and a process is the part
that can hang, fail, or write something unexpected. The rules about what a
version *means* live in `history.py`, which touches nothing.

Two things this module will not do:

- **It will not run `git reset`, `checkout` or `clean`.** Undo is "write this old
  version of this one file", which is the same act as typing the old text back in
  and cannot reach anything the person did not ask it to reach.
- **It will not guess.** A revision that is not a hex sha, a path that leaves the
  vault, and a vault that is not a repository are each answered with a sentence.
"""

from __future__ import annotations

import os
import re

from . import history, proc

#: A revision this app will accept: what `git log` printed, and nothing else. No
#: `HEAD~3`, no `main@{yesterday}` -- the app has no business resolving revision
#: syntax, and a sha is what the UI has in hand.
REV_RE = re.compile(r"^[0-9a-f]{7,40}$")

#: A first commit says what it is, because it will sit at the bottom of the log
#: forever next to real changes.
FIRST_COMMIT = "history begins: the vault as it was"

#: A checkpoint is the app recording drift it did not record piece by piece; the
#: message must not pretend to be a single deliberate edit.
CHECKPOINT_MESSAGE = "checkpoint: the vault as it is"


def _git(root: str, args: list[str]) -> tuple[str, str, int]:
    return proc.run(["git", "-C", str(root), *args])


def _ask(root: str, args: list[str]) -> tuple[bool, str, str]:
    """Run git and say whether it worked. `(ok, stdout, problem)`."""
    out, err, code = _git(root, args)
    if code != 0:
        return False, out, err or f"git exited {code}"
    return True, out, ""


def is_repo(root: str) -> bool:
    ok, _, _ = _ask(root, ["rev-parse", "--git-dir"])
    return ok


def ensure_repo(root: str) -> dict:
    """Turn history on: a repository, an ignore file, and one commit of the vault.

    The baseline commit matters. Without it the *first* edit would be the first
    commit, which would make the state before that edit — the state everything
    you care about is currently in — the one version with no way back to.
    """
    created = []
    if not is_repo(root):
        ok, _, problem = _ask(root, ["init", "-q", "-b", "main"])
        if not ok:
            return {"ok": False, "why": problem}
        created.append("repository")

    ignore = os.path.join(str(root), ".gitignore")
    if not os.path.exists(ignore):
        try:
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write(history.VAULT_GITIGNORE)
        except OSError as error:
            return {"ok": False, "why": str(error)}
        created.append(".gitignore")

    ok, _, problem = _ask(root, ["add", "-A"])
    if not ok:
        return {"ok": False, "why": problem}
    if not _anything_staged(root):
        return {"ok": True, "created": created, "committed": None,
                "why": "nothing has changed since the last commit"}

    ok, _, problem = _commit(root, FIRST_COMMIT)
    if not ok:
        return {"ok": False, "why": problem}
    _, sha, _ = _ask(root, ["rev-parse", "--short", "HEAD"])
    return {"ok": True, "created": created, "committed": sha.strip(), "why": ""}


def commit_change(root: str, relpaths, act: str, title: str) -> dict:
    """Record one change to one note. Never the app's reason to fail a save.

    `relpaths` is every path the change touched, because a note whose collection
    changed is a note that moved: staging only the new path would leave the old
    file's deletion out of the commit, and the history would show a note existing
    in two places at once.
    """
    if not is_repo(root):
        return {"ok": False, "why": "history is not on for this vault", "off": True}

    safe = []
    for rel in relpaths or []:
        cleaned = history.safe_relpath(root, rel)
        if cleaned:
            safe.append(cleaned)
    if not safe:
        return {"ok": False, "why": "no path inside the vault to record"}

    ok, _, problem = _ask(root, ["add", "-A", "--", *safe])
    if not ok:
        return {"ok": False, "why": problem}
    if not _anything_staged(root):
        # A save that changed nothing — the same text, the same move already
        # recorded. An empty commit would be noise in a log people read.
        return {"ok": True, "committed": None, "why": "nothing changed"}
    ok, _, problem = _commit(root, history.commit_message(act, title))
    if not ok:
        return {"ok": False, "why": problem}
    _, sha, _ = _ask(root, ["rev-parse", "--short", "HEAD"])
    return {"ok": True, "committed": sha.strip(), "why": ""}


def _anything_staged(root: str) -> bool:
    out, _, code = _git(root, ["diff", "--cached", "--name-only"])
    return code == 0 and bool(out.strip())


def _commit(root: str, message: str) -> tuple[bool, str, str]:
    """Commit what is staged, as the app, on a machine with no git identity.

    The identity is passed inline rather than configured into the vault's repo:
    this app should not add a `user.email` to a repository it did not create, and
    it should not depend on one being there. The author is `nookboard`, which is
    true -- nobody else wrote these commits.
    """
    return _ask(root, [
        "-c", f"user.name={history.COMMIT_NAME}",
        "-c", f"user.email={history.COMMIT_EMAIL}",
        "commit", "-q", "--no-verify", "-m", message,
    ])


def pending(root: str) -> list[str]:
    """Paths that have changed since the last recorded change.

    Not every write in this app is recorded precisely -- reordering a column and
    editing dependencies are frontmatter-only writes, and a commit per drag would
    be a log nobody reads. They are not thrown away though: they show up here,
    and the history view offers to record them. Unrecorded is a state the app
    says out loud rather than a gap it hopes nobody notices.
    """
    ok, out, _ = _ask(root, ["status", "--porcelain"])
    if not ok:
        return []
    paths = []
    for line in out.splitlines():
        entry = line[3:].strip()
        if " -> " in entry:  # a rename reads `old -> new`
            entry = entry.split(" -> ", 1)[1]
        if entry:
            paths.append(entry.strip('"'))
    return paths


def checkpoint(root: str) -> dict:
    """Record everything that has changed, because someone asked.

    The safety valve for the changes the app does not record one at a time: it
    stages whatever is there and commits it with a message that says exactly what
    kind of commit this is.
    """
    ok, _, problem = _ask(root, ["add", "-A"])
    if not ok:
        return {"ok": False, "why": problem}
    if not _anything_staged(root):
        return {"ok": True, "committed": None, "why": "nothing changed"}
    ok, _, problem = _commit(root, CHECKPOINT_MESSAGE)
    if not ok:
        return {"ok": False, "why": problem}
    _, sha, _ = _ask(root, ["rev-parse", "--short", "HEAD"])
    return {"ok": True, "committed": sha.strip(), "why": ""}


def log_for(root: str, relpath: str | None = None, limit: int = 30) -> list[dict]:
    """Recent changes, newest first — the whole vault's, or one note's."""
    args = ["log", f"-n{int(limit)}", f"--format={history.log_format()}"]
    if relpath:
        args += ["--", relpath]
    ok, out, _ = _ask(root, args)
    if not ok:
        return []
    return history.parse_log(out)


def deletions(root: str, limit: int = 20) -> list[dict]:
    """Notes this vault has lost, newest first, with the version to bring back.

    The parent of a deletion commit is where the text still lives, so that is
    what gets recorded — not the deletion itself, which contains no content.
    """
    ok, out, _ = _ask(root, [
        "log", f"-n{int(limit)}", "--diff-filter=D", "--name-only",
        f"--format={history.delete_format()}",
    ])
    if not ok:
        return []
    return history.parse_deletions(out)


def matches_now(root: str, relpath: str, rev: str) -> bool:
    """Is the file on disk *exactly* the version at `rev`?

    Measured rather than assumed. A note edited outside the app is not its newest
    commit, and a panel that called the newest commit "the version you have" would
    be telling the person they are looking at something they are not.
    """
    rel = history.safe_relpath(root, relpath)
    if not rel or not REV_RE.match((rev or "").strip()):
        return False
    _, _, code = _git(root, ["diff", "--quiet", rev, "--", rel])
    return code == 0


def read_version(root: str, rev: str, relpath: str) -> tuple[str | None, str]:
    """One file as it was at one revision. `(text, problem)`."""
    rev = (rev or "").strip()
    if not REV_RE.match(rev):
        return None, "not a version this app can read"
    rel = history.safe_relpath(root, relpath)
    if not rel:
        return None, "that path is not inside the vault"
    out, err, code = _git(root, ["show", f"{rev}:{rel}"])
    if code != 0:
        return None, err or f"git exited {code}"
    return out, ""


def restore_version(root: str, relpath: str, rev: str) -> dict:
    """Put one note back the way it was, then record that it was put back.

    A restore is an edit: the file is written, and the write is committed like
    any other change. Undo being undoable is the difference between a history you
    can experiment with and one you are afraid of.
    """
    rel = history.safe_relpath(root, relpath)
    if not rel:
        return {"ok": False, "why": "that path is not inside the vault"}
    text, problem = read_version(root, rev, rel)
    if text is None:
        return {"ok": False, "why": problem}
    target = os.path.join(str(root), rel)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as error:
        return {"ok": False, "why": str(error)}
    title = os.path.splitext(os.path.basename(rel))[0]
    recorded = commit_change(root, [rel], "restore", title)
    return {"ok": True, "path": rel, "restored": rev, "committed": recorded.get("committed")}
