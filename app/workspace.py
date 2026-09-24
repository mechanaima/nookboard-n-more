"""What a folder full of code is, said honestly.

A workspace is a note that points at a directory (`path:`). This module decides
what that directory *is*: a repo or not, which branch, what is uncommitted, when
the last commit landed, and what markers the code still carries. It is the only
place that answers those questions, so the view, a future card and any script
all read the same answer instead of each forming an opinion.

Pure on purpose. It builds git's argv and parses git's output; it scans text for
markers; it decides what "needs attention" means. `workspace_run.py` is the only
part that touches a disk or spawns anything.

Two rules inherited from the rest of the app:

- **No mtime, ever.** "Modified 3 minutes ago" is what the filesystem thinks,
  and it counts things git was never told about. A workspace reports *committed*
  state: the last commit, and what has changed since. See `models.py`:283 for
  why this app refuses the other kind of answer.
- **Say which thing is missing.** Not a repo, no commits yet, no upstream, git
  not installed, the terminal you'd want is not here — each gets its own words
  rather than a blank.
"""

from __future__ import annotations

import os
import posixpath
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------- vocabulary

WORKSPACES_COLLECTION = "workspaces"

#: Field separators inside one git format string. A commit subject can contain
#: anything, including the characters people normally pick as delimiters, so the
#: format string uses ASCII unit/record separators that cannot appear in it.
US = "\x1f"
RS = "\x1e"

#: Directories never worth walking into: dependencies and build output, not the
#: user's code. Sorted so the walk order (and so the marker order) is stable.
SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "target",
        "dist", "build", "__pycache__", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".tox", ".idea", ".vscode", "site-packages", ".cache",
        "vendor", ".next", ".nuxt", "coverage", "htmlcov", ".eggs",
    }
)

#: Extensions worth reading for markers and worth naming in a language count.
#: Markdown and JSON are deliberately absent: prose *about* a TODO is not a
#: TODO in the code, and that is most of the noise people complain about.
CODE_EXTENSIONS = frozenset(
    {
        "py", "sh", "bash", "zsh", "fish", "js", "mjs", "cjs", "ts", "tsx",
        "jsx", "rs", "go", "rb", "pl", "php", "java", "kt", "c", "h", "cc",
        "cpp", "hpp", "cs", "swift", "lua", "r", "sql", "vim", "el", "toml",
        "yaml", "yml", "ini", "cfg", "conf", "mk", "just", "dockerfile",
    }
)

LANGUAGE_NAMES = {
    "py": "Python", "sh": "Shell", "bash": "Shell", "zsh": "Shell",
    "fish": "Shell", "js": "JavaScript", "mjs": "JavaScript",
    "cjs": "JavaScript", "ts": "TypeScript", "tsx": "TypeScript",
    "jsx": "JavaScript", "rs": "Rust", "go": "Go", "rb": "Ruby",
    "pl": "Perl", "php": "PHP", "java": "Java", "kt": "Kotlin", "c": "C",
    "h": "C", "cc": "C++", "cpp": "C++", "hpp": "C++", "cs": "C#",
    "swift": "Swift", "lua": "Lua", "r": "R", "sql": "SQL", "vim": "Vim script",
    "el": "Emacs Lisp", "toml": "TOML", "yaml": "YAML", "yml": "YAML",
    "ini": "INI", "cfg": "Config", "conf": "Config", "mk": "Make",
    "just": "just", "dockerfile": "Docker",
}

#: Uppercase only, and in a comment. Both halves of that are the point:
#:
#: - Lowercase "todo" is prose most of the time — every docstring that mentions
#:   a list of things to do would be swept up. A marker is a convention the
#:   person wrote on purpose.
#: - The marker must sit *behind a comment introducer*, because `Stage.TODO` is
#:   an identifier, `TODO = "todo"` is an enum member, and `TODOS = []` is a
#:   variable. The first version matched all three, and produced 22 "markers" in
#:   a repo that has a handful — a list you have to filter is one you stop
#:   reading, which is the whole reason to have the list.
MARKER_RE = re.compile(
    r"(?:^|\s)(?:#|//|/\*|\*/|--|;|<!--|>|\*)\s*(TODO|FIXME|XXX|HACK)\b[:\-—]?\s*(.*)"
)

MAX_MARKERS = 200
MAX_FILES = 4000
MAX_DEPTH = 6
MAX_MARKER_LINE = 120

#: A repo that has not been committed to in this long is worth a look, and a
#: number has to be chosen somewhere: three weeks is long enough that a normal
#: week off does not trip it. It is a chosen threshold, not a law.
STALE_DAYS = 21


# ------------------------------------------------------------------- paths


def clean_path(raw) -> str | None:
    """The directory a note means, as a usable path.

    `~` expands and `..` resolves, because a note is written by hand and a
    hand-written path is allowed to be relative to home. An empty value means
    no path at all, which is not an error: most notes have none.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return os.path.normpath(os.path.expanduser(text))


def display_path(path: str, home: str | None = None) -> str:
    """A path shortened for reading, with `~` for home.

    Done on the server because the server is what knows where home is; a client
    guessing at `/home/<name>` would be wrong for whoever is not the first user
    of the machine. Only the home prefix is shortened — a path with `…` in the
    middle is one you cannot type back into a terminal.
    """
    home = (home or os.path.expanduser("~")).rstrip(os.sep)
    if home and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def name_for(path: str) -> str:
    """What to call a workspace whose note has no title: its folder's name."""
    return posixpath.basename(path.rstrip(os.sep)) or path


def path_field(note) -> str | None:
    """The path a note carries, cleaned, or None. Accepts a dict or a note."""
    raw = note.get("path") if isinstance(note, dict) else getattr(note, "path", None)
    return clean_path(raw)


def is_workspace_note(note) -> bool:
    """A note is a workspace when it points somewhere. One rule, no marker tag."""
    return path_field(note) is not None


def prefix_for(path: str, repo_root: str) -> str:
    """A folder's prefix inside its repo, or "" when the folder *is* the repo.

    A folder can be inside a repo without being one — `Linux/Scripts` is a
    directory of shell scripts that happens to live in the School vault repo.
    Saying "12 uncommitted" there would be counting the vault, not the folder.
    """
    folder = os.path.relpath(os.path.realpath(path), os.path.realpath(repo_root))
    if folder in (".", "", os.sep):
        return ""
    return folder.rstrip(os.sep) + "/"


def entries_under(entries, prefix: str) -> list[str]:
    """The changed paths inside one folder. An empty prefix keeps all of them."""
    if not prefix:
        return list(entries)
    trimmed = prefix.rstrip("/")
    return [e for e in entries if e == trimmed or e.startswith(prefix)]


# --------------------------------------------------------------------- git


def git_args(path: str, what: str) -> list[str]:
    """The argv for one question. `-C` makes the path the repo, not the cwd."""
    if what == "toplevel":
        return ["git", "-C", path, "rev-parse", "--show-toplevel"]
    if what == "status":
        return [
            "git", "-C", path, "status", "--porcelain=v2", "--branch",
            "--untracked-files=normal",
        ]
    if what == "head":
        return [
            "git", "-C", path, "log", "-1",
            f"--format=%H{US}%cI{US}%an{US}%s",
        ]
    raise ValueError(f"unknown git question: {what!r}")


def parse_status(text: str) -> dict:
    """Read `status --porcelain=v2 --branch`.

    Only counts and the branch facts come out of here, plus the changed paths
    themselves (a count you cannot look at is a count you cannot act on). The
    format is stable and line-oriented, which is why it is asked for in v2 with
    an explicit `--branch`, rather than the human output.
    """
    out: dict = {
        "branch": None,
        "detached": False,
        "upstream": None,
        "ahead": None,
        "behind": None,
        "changed": [],
        "untracked": [],
        "staged": 0,
        "modified": 0,
        "conflicted": 0,
    }
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            if head == "(detached)":
                out["detached"] = True
            else:
                out["branch"] = head
        elif line.startswith("# branch.upstream "):
            out["upstream"] = line[len("# branch.upstream "):].strip()
        elif line.startswith("# branch.ab "):
            # "+2 -1": how far ahead and behind the upstream this branch is.
            parts = line[len("# branch.ab "):].split()
            for part in parts:
                if part.startswith("+") and part[1:].isdigit():
                    out["ahead"] = int(part[1:])
                elif part.startswith("-") and part[1:].isdigit():
                    out["behind"] = int(part[1:])
        elif line.startswith("? "):
            out["untracked"].append(line[2:])
        elif line[:2] in ("1 ", "2 ", "u "):
            kind, rest = line[0], line[2:]
            fields = rest.split(" ")
            if len(fields) < 8:
                continue
            xy = fields[0]
            # The path is not at a fixed index. Porcelain v2 puts it after the
            # hashes, and an unmerged record carries four modes and *three*
            # hashes (m1 m2 m3 h1 h2 h3) where an ordinary record carries three
            # modes and two. Reading index 8 blindly returned a blob hash for a
            # conflicted file -- a bug found by parsing real output, and never by
            # a sample written from memory of the format.
            if kind == "u":
                where = fields[9] if len(fields) > 9 else ""
            elif len(fields) > 8:  # a rename or copy adds <X><score> before the path
                where = fields[8]
            else:
                where = fields[7]
            if "\t" in where:
                where = where.split("\t")[0]
            if not where:
                continue
            out["changed"].append(where)
            if kind == "u":
                out["conflicted"] += 1
            else:
                if xy[0] != ".":
                    out["staged"] += 1
                if xy[1] != ".":
                    out["modified"] += 1
    return out


def parse_last_commit(text: str) -> dict | None:
    """Read the one-line `%H %cI %an %s` record. None when there is no commit."""
    text = text.strip()
    if not text:
        return None
    parts = text.split(US)
    if len(parts) < 4:
        return None
    return {"sha": parts[0], "when": parts[1], "author": parts[2], "subject": parts[3]}


# ------------------------------------------------------------------- time


def age_days(when: str | None, now: datetime) -> float | None:
    """Whole days since an ISO timestamp, or None when there is nothing to age."""
    moment = parse_moment(when)
    if moment is None:
        return None
    return (now - moment).total_seconds() / 86400.0


def parse_moment(when: str | None) -> datetime | None:
    """An ISO datetime from git, as an aware datetime. Git says `+02:00`."""
    if not when:
        return None
    text = str(when).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _plural(count: int, unit: str) -> str:
    """One of a thing, or several — the noun carries the s, not the sentence."""
    return f"1 {unit} ago" if count == 1 else f"{count} {unit}s ago"


def relative_age(when: str | None, now: datetime) -> str:
    """How long ago, in the words a person would use. Computed once, server-side.

    The client renders this string rather than deriving it, so there is one
    arithmetic for age rather than two that can disagree about "a month".

    Each unit is floored, and the boundaries are the unit's own length: under 24
    hours is hours, under 14 days is days, and so on. The first version branched
    on `< 36` hours and rounded, which made **"1 day ago" unreachable** — every
    age that would have rounded to one day was still being called hours.
    """
    moment = parse_moment(when)
    if moment is None:
        return "never"
    seconds = (now - moment).total_seconds()
    if seconds < 0:
        # A commit timestamp in the future is a clock problem, not a fact.
        return "in the future"
    if seconds < 90:
        return "just now"
    minutes = seconds / 60.0
    if minutes < 90:
        return _plural(max(1, round(minutes)), "minute")
    hours = seconds / 3600.0
    if hours < 24:
        return _plural(max(1, int(hours)), "hour")
    days = seconds / 86400.0
    if days < 14:
        return _plural(max(1, int(days)), "day")
    weeks = days / 7.0
    if weeks < 9:
        return _plural(max(1, int(weeks)), "week")
    months = days / 30.44
    if months < 24:
        return _plural(max(1, int(months)), "month")
    return _plural(max(1, int(days / 365.25)), "year")


# ----------------------------------------------------------------- markers


def marker_kind(line: str) -> tuple[str, str] | None:
    """The first marker on a line, and what it says after it.

    It has to be a real comment: an introducer that is sitting inside a string
    literal is data, not a note-to-self -- `("# FIXME: this loops forever",
    "FIXME")` in a test is not a FIXME. An odd number of quotes before the
    introducer is the cheapest way to tell, and it is a heuristic: a marker
    following an apostrophe inside a real comment is missed. Missing one is the
    better error, because the app only ever prints the text it actually found --
    a marker that is not there is quieter than a quote that is not there.
    """
    for match in MARKER_RE.finditer(line):
        prefix = match.group(0)
        introducer = match.start() + (len(prefix) - len(prefix.lstrip()))
        before = line[:introducer]
        if before.count('"') % 2 or before.count("'") % 2:
            continue
        said = match.group(2).strip()
        # A marker used as a comment banner with nothing after it is still one.
        said = said.strip("#/-*<! \t")
        if len(said) > MAX_MARKER_LINE:
            said = said[: MAX_MARKER_LINE - 1].rstrip() + "…"
        return match.group(1), said
    return None


def markers_in(text: str, relpath: str, limit: int = MAX_MARKERS) -> list[dict]:
    """Every marker in one file's text, with the line number it is on."""
    found: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        hit = marker_kind(line)
        if hit is None:
            continue
        found.append(
            {"file": relpath, "line": number, "kind": hit[0], "text": hit[1]}
        )
        if len(found) >= limit:
            break
    return found


def is_code_file(name: str) -> bool:
    """Worth reading for markers: a code file, and not a binary or a lockfile."""
    lowered = name.lower()
    if lowered in ("dockerfile", "makefile", "justfile"):
        return True
    if lowered.endswith(".lock") or lowered in ("package-lock.json", "poetry.lock"):
        return False
    extension = lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    return extension in CODE_EXTENSIONS


def count_languages(names: list[str]) -> dict:
    """A code file count by language, biggest first. Empty ones are left out."""
    counts: dict = {}
    for name in names:
        if not is_code_file(name):
            continue
        extension = name.lower().rsplit(".", 1)[-1] if "." in name else name.lower()
        language = LANGUAGE_NAMES.get(extension, extension)
        counts[language] = counts.get(language, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ------------------------------------------------------------------- state


def state_words(state: dict) -> str:
    """The one line a person reads. Says what is wrong before what is fine."""
    if state.get("missing"):
        return "that folder is gone"
    if state.get("problem"):
        return state["problem"]
    if not state.get("is_repo"):
        count = state.get("file_count") or 0
        return f"{count} files, not a git repo" if count else "not a git repo"
    if not state.get("has_commits"):
        uncommitted = state.get("uncommitted") or 0
        if uncommitted:
            return f"no commits yet, {uncommitted} waiting"
        return "no commits yet"
    pieces = []
    uncommitted = state.get("uncommitted") or 0
    if uncommitted:
        # "here" matters when the folder is inside a bigger repo: the number is
        # about this folder, the repo is what would take the commit.
        pieces.append(f"{uncommitted} uncommitted here" if state.get("nested")
                      else f"{uncommitted} uncommitted")
    pieces.append(state.get("age_text") or "never committed")
    if state.get("nested") and state.get("repo_name"):
        pieces.append(f"in the {state['repo_name']} repo")
    line = " · ".join(pieces)
    if state.get("conflicted"):
        line = f"{state['conflicted']} conflicted · {line}"
    return line


def attention_reasons(state: dict) -> list[str]:
    """Why this workspace might want you, in order of how much it matters.

    Deliberately short. A list that flags everything flags nothing, so this is
    uncommitted work and a long silence, and nothing else.
    """
    reasons: list[str] = []
    if state.get("problem") or state.get("missing"):
        return reasons
    uncommitted = state.get("uncommitted") or 0
    if state.get("conflicted"):
        reasons.append(f"{state['conflicted']} conflicted files")
    if uncommitted:
        reasons.append(f"{uncommitted} uncommitted")
    days = state.get("age_days")
    if state.get("is_repo") and days is not None and days >= STALE_DAYS:
        reasons.append(f"no commit in {int(days)} days")
    if state.get("is_repo") and not state.get("has_commits"):
        reasons.append("nothing committed yet")
    return reasons


def sort_states(states: list[dict]) -> list[dict]:
    """The order a person wants: work first, then what is broken, then settled.

    Uncommitted work sorts above a folder that has gone missing, because the
    question a workspace list answers is "which of these needs me today" — and a
    note pointing at a moved folder is a tidy-up, not work. Within each band the
    more wrong comes first, and ties are broken by name so the order is stable.
    """
    def key(state: dict):
        # Work (0) above a folder that is broken (1) above one that is settled
        # (2); within a band, the more wrong sorts first.
        band = 2 if not needs_attention(state) else (0 if state.get("reasons") else 1)
        return (
            band,
            -len(state.get("reasons") or []),
            -(state.get("uncommitted") or 0),
            (state.get("note_title") or state.get("name") or "").lower(),
        )

    return sorted(states, key=key)


def needs_attention(state: dict) -> bool:
    """Whether a person has something to do here.

    Uncommitted work counts, and so does a folder that has gone missing or a git
    that will not run. "Settled" has to mean *nothing wants you* -- a folder that
    is not there is not settled, it is unknown, and a dashboard that called it
    settled would be reassuring someone about a folder it never found.
    """
    return bool(state.get("reasons")) or bool(state.get("missing")) or bool(state.get("problem"))


def summarize(states: list[dict]) -> dict:
    """The whole set at a glance, for a card or a line of text."""
    return {
        "total": len(states),
        "need_attention": len([s for s in states if needs_attention(s)]),
        "dirty": len([s for s in states if (s.get("uncommitted") or 0) > 0]),
        "broken": len([s for s in states
                       if s.get("missing") or s.get("problem")]),
        "markers": sum(len(s.get("markers") or []) for s in states),
    }


def attention_line(summary: dict) -> str:
    """The sentence a card carries. Honest when there is nothing to say."""
    if not summary["total"]:
        return "no workspaces yet"
    if not summary["need_attention"]:
        return f"all {summary['total']} workspaces are settled"
    count = summary["need_attention"]
    which = "workspace needs" if count == 1 else "workspaces need"
    return f"{count} {which} attention"


# ---------------------------------------------------------------- running it


#: The editor and terminals to try, best first. Resolved at runtime, and an
#: empty answer is reported rather than guessed at.
EDITOR_CANDIDATES = ("code", "code-oss", "codium", "codium-insiders")
TERMINAL_CANDIDATES = ("alacritty", "kitty", "wezterm", "mate-terminal",
                       "gnome-terminal", "konsole", "xterm")
FILE_CANDIDATES = ("xdg-open",)

OPEN_ACTIONS = ("editor", "terminal", "files")


def inside_folder(folder: str, relpath) -> str | None:
    """The file inside a folder that a request asked to open, or `None`.

    A card sends `file` to say *which* file to look at, which means the value
    comes from the request -- so the only paths this app will ever open are ones
    that resolve to a real file strictly inside the workspace's own folder.
    `../../etc/passwd` and `/etc/passwd` are refused rather than sanitized: a
    sanitized path is still a request choosing a file, and the honest thing is to
    say no.

    A leading `-` is refused too. `code --wait` is an argument, not a file, and
    reaching it by naming a "file" is the one way a validated-looking path can
    still become an option flag.
    """
    if not isinstance(relpath, str):
        return None
    candidate = relpath.strip()
    if not candidate or candidate.startswith("-"):
        return None
    root = os.path.realpath(folder)
    joined = os.path.realpath(os.path.join(root, candidate))
    # `!= root` on its own would allow the folder itself; `startswith(root)` on
    # its own would allow a sibling named like `nookboard-notes`.
    if joined != root and not joined.startswith(root + os.sep):
        return None
    if joined == root or not os.path.isfile(joined):
        return None
    return joined


def editor_args(binary: str, path: str, file: str | None = None,
                line: int | None = None) -> list[str]:
    """Open a folder -- or one file in it -- in an editor.

    `--goto FILE:LINE` is how the editors on this list are told to put the
    cursor somewhere, and it is the same flag for all of them, which is the only
    reason it is safe to have one shape here.
    """
    if file:
        return [binary, "--goto", f"{file}:{line}" if line else file]
    return [binary, path]


def terminal_args(binary: str, path: str) -> list[str]:
    """Open a terminal *in* a folder. Each one spells that differently, which is
    exactly the kind of detail worth having one tested implementation of."""
    name = posixpath.basename(binary)
    if name == "alacritty":
        return [binary, "--working-directory", path]
    if name in ("kitty", "wezterm", "konsole"):
        return [binary, "--working-directory", path]
    if name == "mate-terminal":
        return [binary, f"--working-directory={path}"]
    if name == "gnome-terminal":
        return [binary, f"--working-directory={path}"]
    if name == "xterm":
        # xterm has no working-directory flag: it must be told to cd first.
        return [binary, "-e", "sh", "-c", f'cd "{path}" && exec "${{SHELL:-sh}}"']
    return [binary, path]


def file_args(binary: str, path: str) -> list[str]:
    """Open a folder in the desktop's file manager."""
    return [binary, path]


def open_args(what: str, binary: str, path: str, file: str | None = None,
              line: int | None = None) -> list[str]:
    """The argv for one open action. Unknown actions are refused, not guessed.

    `file` is an absolute path already checked by `inside_folder` -- this
    function builds argv, it does not vet anything. A terminal ignores it on
    purpose: a terminal is opened *at* a folder, and the argv the server reports
    is what makes that visible rather than silent.
    """
    if what == "editor":
        return editor_args(binary, path, file=file, line=line)
    if what == "terminal":
        return terminal_args(binary, path)
    if what == "files":
        return file_args(binary, file or path)
    raise ValueError(f"cannot open {what!r}")


def candidates_for(what: str) -> tuple[str, ...]:
    if what == "editor":
        return EDITOR_CANDIDATES
    if what == "terminal":
        return TERMINAL_CANDIDATES
    if what == "files":
        return FILE_CANDIDATES
    raise ValueError(f"cannot open {what!r}")


def missing_tool_words(what: str) -> str:
    """What to say when nothing can do the job. Names the thing that is missing."""
    if what == "files":
        return "no file manager found (looked for xdg-open)"
    names = ", ".join(candidates_for(what))
    return f"no {what} found (looked for {names})"
