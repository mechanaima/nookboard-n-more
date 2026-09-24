"""The part of a workspace that runs things.

Everything here touches the disk or spawns a process, and it is deliberately the
smallest possible surface: run one git question, walk one tree, open one folder.
The rules about what any of it *means* live in `workspace.py`, which has no I/O
and can be tested without a repo on disk.

Nothing here writes. A workspace note **reads** the directory it points at — the
app has no business committing, staging or cleaning anything on your behalf.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

from . import workspace
from .transcribe import resolve_tool

#: git answers these in milliseconds; a hung git (a network filesystem, a stuck
#: lock) must not hang the request that asked.
GIT_TIMEOUT = 15

#: Markers are read by walking. A big vendored tree is not worth a slow page, so
#: the walk is capped and says how many files it stopped counting at.
MAX_FILE_BYTES = 512 * 1024


def _run(argv: list[str], timeout: int = GIT_TIMEOUT) -> tuple[str, str, int]:
    """Run one command. Returns (stdout, stderr, code); a missing binary or a
    timeout comes back as a problem string rather than an exception, because
    "git is not installed" is a thing to say, not a thing to crash on."""
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        return "", f"{argv[0]} is not installed", 127
    except subprocess.TimeoutExpired:
        return "", f"{argv[0]} did not answer within {timeout}s", 124
    except OSError as error:  # a bad path, a permission problem
        return "", str(error), 126
    return done.stdout, done.stderr.strip(), done.returncode


def git(path: str, what: str) -> tuple[str, str]:
    """Ask git one question about one directory. (stdout, problem)."""
    out, err, code = _run(workspace.git_args(path, what))
    if code != 0:
        # `rev-parse --show-toplevel` says "not a git repository" on stderr and
        # exits 128 -- that is an answer, not a failure, so the caller decides.
        return "", err or f"git exited {code}"
    return out, ""


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def walk(path: str) -> dict:
    """One bounded walk of the tree: file names, languages, and markers.

    A single walk rather than three, because the three answers are all about the
    same set of files and three walks can disagree about a tree that changed
    mid-page.
    """
    names: list[str] = []
    markers: list[dict] = []
    truncated = False
    root_depth = path.rstrip(os.sep).count(os.sep)
    for current, dirs, files in os.walk(path):
        depth = current.rstrip(os.sep).count(os.sep) - root_depth
        dirs[:] = sorted(d for d in dirs
                         if d not in workspace.SKIP_DIRS and not d.startswith("."))
        if depth >= workspace.MAX_DEPTH:
            dirs[:] = []
        for name in sorted(files):
            if name.startswith("."):
                continue
            names.append(name)
            if len(names) > workspace.MAX_FILES:
                truncated = True
                break
            if len(markers) >= workspace.MAX_MARKERS or not workspace.is_code_file(name):
                continue
            full = os.path.join(current, name)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            relpath = os.path.relpath(full, path)
            room = workspace.MAX_MARKERS - len(markers)
            markers.extend(
                workspace.markers_in(_read_text(full), relpath, limit=room)
            )
        if truncated:
            break
    return {
        "file_count": len(names),
        "languages": workspace.count_languages(names),
        "markers": markers,
        "walk_limited": truncated,
    }


def read_path(path: str, now: datetime | None = None) -> dict:
    """Everything the app knows about one directory. Never raises for a bad one.

    A state always carries `words` (the one line to show) and `reasons` (why it
    might want you), so a caller never has to invent wording of its own.
    """
    now = now or datetime.now(timezone.utc)
    state: dict = {
        "path": path,
        "display_path": workspace.display_path(path),
        "name": workspace.name_for(path),
    }

    if not os.path.isdir(path):
        state.update(
            {
                "exists": False,
                "missing": True,
                "problem": "that folder is gone",
                "is_repo": False,
                "markers": [],
                "reasons": [],
            }
        )
        state["words"] = workspace.state_words(state)
        return state

    state["exists"] = True
    top, problem = git(path, "toplevel")
    if not top:
        # Not a repo is a normal answer -- a folder of shell scripts does not
        # have to be one. `problem` here is git's own "not a git repository".
        walked = walk(path)
        state.update(
            {
                "is_repo": False,
                "has_commits": False,
                "git_problem": problem,
                "uncommitted": 0,
                "reasons": [],
                **walked,
            }
        )
        state["words"] = workspace.state_words(state)
        return state

    repo_root = top.strip()
    # Ask git at the repo *root*, not at the folder. `git -C <subdir> status`
    # reports paths relative to that subdir -- untracked entries came back as
    # "./" and "../elsewhere.py" -- so a folder-relative filter could never match
    # and the counts silently read zero. One place to ask, and paths that mean
    # the same thing everywhere.
    status_text, status_problem = git(repo_root, "status")
    head_text, _ = git(repo_root, "head")
    parsed = workspace.parse_status(status_text) if status_text else {}
    if not parsed:
        state["problem"] = status_problem or "git did not report the state"
        parsed = {}
    last = workspace.parse_last_commit(head_text)
    changed = list(parsed.get("changed") or [])
    untracked = list(parsed.get("untracked") or [])
    walked = walk(path)

    # A folder inside a repo reports *its own* uncommitted work, not the whole
    # repo's: the two are different questions and the answer must say which one
    # it is answering. The repo-wide count is kept beside it for context.
    prefix = workspace.prefix_for(path, repo_root)
    here_changed = workspace.entries_under(changed, prefix)
    here_untracked = workspace.entries_under(untracked, prefix)

    state.update(
        {
            "is_repo": True,
            "repo_root": repo_root,
            "repo_name": workspace.name_for(repo_root),
            "nested": bool(prefix),
            "branch": parsed.get("branch"),
            "detached": bool(parsed.get("detached")),
            "upstream": parsed.get("upstream"),
            "ahead": parsed.get("ahead"),
            "behind": parsed.get("behind"),
            "staged": parsed.get("staged", 0),
            "modified": parsed.get("modified", 0),
            "conflicted": parsed.get("conflicted", 0),
            "changed": here_changed[:20],
            "untracked": here_untracked[:20],
            "repo_uncommitted": len(changed) + len(untracked),
            "has_commits": last is not None,
            "uncommitted": len(here_changed) + len(here_untracked),
            "last": last,
            "age_days": workspace.age_days(last["when"], now) if last else None,
            "age_text": workspace.relative_age(last["when"], now) if last else "never",
            **walked,
        }
    )
    state["reasons"] = workspace.attention_reasons(state)
    state["words"] = workspace.state_words(state)
    return state


def state_for_note(note, now: datetime | None = None) -> dict | None:
    """The state of the folder a note points at, or None if it points nowhere."""
    path = workspace.path_field(note)
    if path is None:
        return None
    state = read_path(path, now=now)
    title = note.get("title") if isinstance(note, dict) else getattr(note, "title", None)
    note_id = note.get("id") if isinstance(note, dict) else getattr(note, "id", None)
    state["note_id"] = note_id
    state["note_title"] = title or state["name"]
    state["collection"] = (
        note.get("collection") if isinstance(note, dict)
        else getattr(note, "collection", None)
    )
    return state


def which_tool(what: str) -> str:
    """The first installed tool that can do this job, or "".

    Deliberately says *which* of the candidates was found, so the app can tell
    you what it is about to run before it runs it.
    """
    for candidate in workspace.candidates_for(what):
        found = resolve_tool(candidate)
        if found:
            return found
    return ""


def tools() -> dict:
    """What is installed, per action. Reported once so the view can show which
    buttons can actually do anything on this machine."""
    return {what: which_tool(what) for what in workspace.OPEN_ACTIONS}


def open_workspace(path: str, what: str) -> dict:
    """Open a folder in an editor, a terminal, or the file manager.

    Returns the argv it ran so the caller can *say* what it did. Spawned
    detached and with its output discarded: this app is a server, and a child
    that held the pipe open would keep the request alive after the window it
    opened had closed.
    """
    if what not in workspace.OPEN_ACTIONS:
        return {"error": f"cannot open {what!r}", "ok": False}
    if not os.path.isdir(path):
        return {"error": "that folder is gone", "ok": False, "path": path}
    binary = which_tool(what)
    if not binary:
        return {"error": workspace.missing_tool_words(what), "ok": False}
    argv = workspace.open_args(what, binary, path)
    try:
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=path,
        )
    except OSError as error:
        return {"error": str(error), "ok": False, "ran": argv}
    return {"ok": True, "ran": argv, "tool": binary, "what": what, "path": path}
