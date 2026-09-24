"""What a workspace is, and what it says.

The git fixtures here are **real output** captured from this machine, not samples
written from memory of the porcelain v2 format. That distinction earned its keep
immediately: the unmerged-record path index was wrong in a way no hand-written
sample would have shown, because a sample written from memory would have had the
same wrong idea as the parser.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app import workspace as W
from app import workspace_run as R

# --------------------------------------------------------------- real fixtures

# `git -C ~/projects/nookboard status --porcelain=v2 --branch` (untracked files)
CLEAN_STATUS = """# branch.oid bdee4d6ae67f68ec78d4f95c6e868a5b0885f27c
# branch.head main
? app/workspace.py
? app/workspace_run.py
"""

# `git -C ~/projects/readalong status --porcelain=v2 --branch` -- 9 modified
# files and 2 untracked, from a repo that had been left mid-work.
DIRTY_STATUS = """# branch.oid 9a2c4fc8aea8052dd4decf940a001b4a3beaacf0
# branch.head main
1 .M N... 100644 100644 100644 38e9ff9c2ef5b8f6025b638006dbc6417589e321 38e9ff9c2ef5b8f6025b638006dbc6417589e321 .gitignore
1 .M N... 100644 100644 100644 adb953eb650692fefa5b31238e14a97fc95e61f5 adb953eb650692fefa5b31238e14a97fc95e61f5 README.md
1 .M N... 100644 100644 100644 65170dabe603afdb01d001ce391ca97697872914 65170dabe603afdb01d001ce391ca97697872914 app/config.py
1 .M N... 100644 100644 100644 24131eac73e5c8f77fbe822b0bc1d090920486a1 24131eac73e5c8f77fbe822b0bc1d090920486a1 static/js/app.js
1 .M N... 100644 100644 100644 124341690867cef4f0586726b231f6536fb8a032 124341690867cef4f0586726b231f6536fb8a032 uv.lock
? tools/bench_kokoro.py
? tools/kokoro_smoke.py
"""

# The tracking facts, from `git -C ~/Documents/School/Programming/Scripts ...`.
TRACKED_STATUS = """# branch.oid 0be5654b8d720d280cc277b97fbb2dc1f5169132
# branch.head main
# branch.upstream Github/main
# branch.ab +2 -3
? PROG1784-F26/
"""

RENAME_STATUS = (
    "1 .M N... 100644 100644 100644 aaa aaa old/name.py\n"
    "2 R. N... 100644 100644 100644 bbb bbb R100 src/new.py\tsrc/old.py\n"
)

# An unmerged record carries FOUR modes and THREE hashes where an ordinary one
# carries three and two -- the path sits at index 9, not 8.
CONFLICT_STATUS = (
    "# branch.head main\n"
    "u UU N... 100644 100644 100644 100644 "
    "aaaa bbbb cccc src/tangled.py\n"
    "1 .M N... 100644 100644 100644 dddd dddd src/other.py\n"
)

HEAD_LINE = (
    "bdee4d6ae67f68ec78d4f95c6e868a5b0885f27c"
    "\x1f2026-09-24T13:28:34-04:00"
    "\x1fCastor"
    "\x1fA note can say when, and the machine will tell you"
)

NOW = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------- the path


def test_a_note_with_no_path_is_not_a_workspace():
    assert W.path_field({"path": None}) is None
    assert W.path_field({"path": "   "}) is None
    assert W.path_field({}) is None
    assert not W.is_workspace_note({"title": "just a note"})


def test_a_path_expands_home_and_settles_dots():
    assert W.clean_path("~/Documents/School/Linux/Scripts") == os.path.join(
        os.path.expanduser("~"), "Documents/School/Linux/Scripts"
    )
    assert W.clean_path("~/a/b/../c") == os.path.join(os.path.expanduser("~"), "a/c")


def test_a_workspace_is_named_after_its_folder():
    assert W.name_for("/home/x/Documents/School/Linux/Scripts/") == "Scripts"
    assert W.name_for("/home/x/projects/nookboard") == "nookboard"


def test_a_note_object_works_as_well_as_a_dict():
    class Note:
        path = "~/projects/nookboard"
        title = "Nookboard"

    assert W.path_field(Note()) == os.path.join(os.path.expanduser("~"), "projects/nookboard")
    assert W.is_workspace_note(Note())


# ------------------------------------------------------------------ the argv


def test_git_argv_asks_the_question_about_that_path():
    assert W.git_args("/tmp/x", "toplevel") == [
        "git", "-C", "/tmp/x", "rev-parse", "--show-toplevel",
    ]
    status = W.git_args("/tmp/x", "status")
    assert status[:4] == ["git", "-C", "/tmp/x", "status"]
    assert status[4:] == ["--porcelain=v2", "--branch", "--untracked-files=normal"]
    # an explicit --branch and untracked mode, so the parse never depends on
    # whatever the user's git config happens to default to
    assert "--branch" in status and "--untracked-files=normal" in status
    assert W.git_args("/tmp/x", "head")[3:5] == ["log", "-1"]


def test_an_unknown_git_question_is_refused_not_guessed():
    with pytest.raises(ValueError):
        W.git_args("/tmp/x", "diff")


# ------------------------------------------------------------- the status parse


def test_a_status_parse_reads_the_branch_and_the_untracked_files():
    state = W.parse_status(CLEAN_STATUS)
    assert state["branch"] == "main"
    assert state["detached"] is False
    assert state["untracked"] == ["app/workspace.py", "app/workspace_run.py"]
    assert state["changed"] == []


def test_a_status_parse_separates_modified_from_staged_and_keeps_the_paths():
    state = W.parse_status(DIRTY_STATUS)
    assert state["branch"] == "main"
    assert state["changed"] == [
        ".gitignore", "README.md", "app/config.py", "static/js/app.js", "uv.lock",
    ]
    assert state["modified"] == 5
    assert state["staged"] == 0
    assert state["untracked"] == ["tools/bench_kokoro.py", "tools/kokoro_smoke.py"]


def test_a_status_parse_reads_how_far_ahead_and_behind():
    state = W.parse_status(TRACKED_STATUS)
    assert state["upstream"] == "Github/main"
    assert state["ahead"] == 2
    assert state["behind"] == 3
    assert state["untracked"] == ["PROG1784-F26/"]


def test_a_rename_reports_the_new_path_only():
    state = W.parse_status(RENAME_STATUS)
    assert state["changed"] == ["old/name.py", "src/new.py"]


def test_a_conflicted_file_is_named_not_replaced_by_a_blob_hash():
    """The unmerged record's path is at index 9, after three hashes. Reading it
    as an ordinary record returns a 40-character blob hash, which then appears as
    a "changed path" that does not exist."""
    state = W.parse_status(CONFLICT_STATUS)
    assert state["conflicted"] == 1
    assert state["changed"] == ["src/tangled.py", "src/other.py"]
    assert all(not path.startswith("cccc") for path in state["changed"])


def test_a_detached_head_says_so_instead_of_naming_a_branch():
    state = W.parse_status("# branch.head (detached)\n")
    assert state["detached"] is True
    assert state["branch"] is None


def test_an_empty_status_is_a_repo_with_nothing_to_say():
    state = W.parse_status("")
    assert state["branch"] is None and state["changed"] == []


# ------------------------------------------------------------- the last commit


def test_the_last_commit_comes_back_whole():
    last = W.parse_last_commit(HEAD_LINE)
    assert last["sha"].startswith("bdee4d6")
    assert last["author"] == "Castor"
    assert last["subject"] == "A note can say when, and the machine will tell you"
    assert W.parse_moment(last["when"]).utcoffset() == timedelta(hours=-4)


def test_a_repo_with_no_commits_has_no_last_commit():
    assert W.parse_last_commit("") is None
    assert W.parse_last_commit("only one field") is None


# ------------------------------------------------------------------ the clock


@pytest.mark.parametrize(
    "when,expected",
    [
        ("2026-09-24T17:59:40+00:00", "just now"),
        ("2026-09-24T17:30:00+00:00", "30 minutes ago"),
        ("2026-09-24T01:00:00+00:00", "17 hours ago"),
        ("2026-09-23T19:00:00+00:00", "23 hours ago"),
        ("2026-09-21T18:00:00+00:00", "3 days ago"),
        ("2026-08-27T18:00:00+00:00", "4 weeks ago"),
        ("2026-05-24T18:00:00+00:00", "4 months ago"),
        ("2024-06-24T18:00:00+00:00", "2 years ago"),
    ],
)
def test_age_is_said_the_way_a_person_says_it(when, expected):
    assert W.relative_age(when, NOW) == expected


def test_one_unit_is_not_pluralised():
    assert W.relative_age("2026-09-23T12:00:00+00:00", NOW) == "1 day ago"


def test_a_commit_dated_in_the_future_is_not_negative_age():
    """A clock that is wrong is a clock problem. "-3 hours ago" would be the app
    repeating the mistake as if it were a fact about the repo."""
    assert W.relative_age("2026-09-24T21:00:00+00:00", NOW) == "in the future"


def test_no_timestamp_has_no_age():
    assert W.relative_age(None, NOW) == "never"
    assert W.age_days("nonsense", NOW) is None


def test_age_in_days_is_the_number_the_stale_rule_reads():
    assert round(W.age_days("2026-09-03T18:00:00+00:00", NOW), 1) == 21.0


# -------------------------------------------------------------------- markers


def test_a_marker_is_found_with_its_line_number():
    text = "import os\n# TODO: wire the tray icon\nx = 1\n# FIXME: this loops forever\n"
    found = W.markers_in(text, "app/main.py")
    assert [m["line"] for m in found] == [2, 4]
    assert found[0] == {
        "file": "app/main.py", "line": 2, "kind": "TODO", "text": "wire the tray icon",
    }
    assert found[1]["kind"] == "FIXME"


def test_markers_are_uppercase_on_purpose():
    """A lowercase "todo" is prose about a todo list most of the time. A list you
    have to filter is one you stop reading, so the convention is the rule."""
    assert W.markers_in("# todo: not a marker\n", "x.py") == []
    assert W.markers_in("# a todo list lives here\n", "x.py") == []


@pytest.mark.parametrize(
    "line,kind",
    [
        ("# TODO: wire the tray icon", "TODO"),
        ("    # FIXME: this loops forever", "FIXME"),
        ("x = 1  # HACK: works around the race", "HACK"),
        ("// TODO tighten this", "TODO"),
        ("-- TODO: revisit the join", "TODO"),
        ("/* XXX: unsafe cast */", "XXX"),
        ("<!-- TODO: a note for the next reader -->", "TODO"),
    ],
)
def test_a_marker_in_a_comment_counts_whatever_the_comment_syntax(line, kind):
    assert (W.marker_kind(line) or (None,))[0] == kind


@pytest.mark.parametrize(
    "line",
    [
        "Stage.TODO, Stage.DOING,",       # an identifier
        'TODO = "todo"',                  # an enum member
        'label = "To do"',
        "the TODO list lives in the app",  # prose with no comment marker
    ],
)
def test_an_identifier_is_not_a_marker(line):
    assert W.marker_kind(line) is None


def test_a_marker_inside_a_word_is_not_a_marker():
    assert W.markers_in("TODOS = []\n", "x.py") == []
    assert W.markers_in("the FIXMES are elsewhere\n", "x.py") == []


def test_marker_shaped_data_is_not_a_marker():
    """The trade-off the rule makes, pinned in both directions.

    A `#`-introduced marker inside quotes is data: this project's own marker
    tests carry lines like the first two below, and reading them as real markers
    puts a syntax error on the card as if it were a note-to-self. What must not
    stop working is a marker after a *balanced* string -- that line is a real
    comment, and a marker is the whole reason to look at the file.
    """
    assert W.marker_kind('        ("    # FIXME: this loops forever", "FIXME"),') is None
    assert W.marker_kind('    "# TODO: not a note to self\n"') is None
    assert W.marker_kind('print("hi")  # TODO: after a string') == ("TODO", "after a string")

    # The honest cost, named rather than hidden: one apostrophe earlier in a real
    # comment and the marker after it is missed.
    assert W.marker_kind("# don't # TODO: this one is missed") is None


def test_a_marker_with_nothing_after_it_is_still_a_marker():
    found = W.markers_in("# TODO\n", "x.py")
    assert found == [{"file": "x.py", "line": 1, "kind": "TODO", "text": ""}]


def test_an_enormous_marker_line_is_cut_not_dropped():
    found = W.markers_in("# TODO: " + "x" * 400 + "\n", "x.py")
    assert len(found[0]["text"]) <= W.MAX_MARKER_LINE
    assert found[0]["text"].endswith("…")


def test_a_file_stops_at_the_marker_cap():
    text = "\n".join(f"# TODO: thing {i}" for i in range(50))
    assert len(W.markers_in(text, "x.py", limit=5)) == 5


@pytest.mark.parametrize(
    "name,code",
    [
        ("main.py", True), ("deploy.sh", True), ("Dockerfile", True),
        ("app.js", True), ("Makefile", True), ("notes.md", False),
        ("data.json", False), ("package-lock.json", False), ("photo.png", False),
        ("uv.lock", False),
    ],
)
def test_what_counts_as_code(name, code):
    assert W.is_code_file(name) is code


def test_languages_are_counted_biggest_first():
    counts = W.count_languages(
        ["a.py", "b.py", "c.sh", "d.ts", "e.md", "f.json", "g.py"]
    )
    assert list(counts)[0] == "Python"
    assert counts["Python"] == 3
    assert counts["Shell"] == 1
    assert "Markdown" not in counts


# ------------------------------------------------------------ what it says


def test_a_missing_folder_says_it_is_gone():
    state = {"missing": True}
    assert W.state_words(state) == "that folder is gone"


def test_a_folder_that_is_not_a_repo_says_how_much_is_in_it():
    assert W.state_words({"is_repo": False, "file_count": 3}) == "3 files, not a git repo"
    assert W.state_words({"is_repo": False, "file_count": 0}) == "not a git repo"


def test_a_repo_with_no_commit_yet_says_so():
    assert W.state_words({"is_repo": True, "has_commits": False}) == "no commits yet"
    assert (
        W.state_words({"is_repo": True, "has_commits": False, "uncommitted": 4})
        == "no commits yet, 4 waiting"
    )


def test_a_settled_repo_is_one_line():
    assert (
        W.state_words(
            {"is_repo": True, "has_commits": True, "uncommitted": 0, "age_text": "6 days ago"}
        )
        == "6 days ago"
    )


def test_uncommitted_work_comes_before_the_clock():
    assert (
        W.state_words(
            {"is_repo": True, "has_commits": True, "uncommitted": 2, "age_text": "17 hours ago"}
        )
        == "2 uncommitted · 17 hours ago"
    )


def test_a_folder_inside_a_repo_says_which_repo():
    """Linux/Scripts is a directory of shell scripts living inside the School
    vault repo. Its uncommitted count is its own; the repo is named because a
    commit there commits the vault."""
    line = W.state_words(
        {
            "is_repo": True, "has_commits": True, "uncommitted": 1,
            "age_text": "2 days ago", "nested": True, "repo_name": "School",
        }
    )
    assert line == "1 uncommitted here · 2 days ago · in the School repo"


def test_conflicts_are_said_first():
    line = W.state_words(
        {"is_repo": True, "has_commits": True, "uncommitted": 3, "conflicted": 2,
         "age_text": "1 day ago"}
    )
    assert line.startswith("2 conflicted · 3 uncommitted")


# ----------------------------------------------------------- wants attention


def test_uncommitted_work_is_a_reason():
    reasons = W.attention_reasons(
        {"is_repo": True, "has_commits": True, "uncommitted": 12, "age_days": 0.7}
    )
    assert reasons == ["12 uncommitted"]


def test_a_long_silence_is_a_reason():
    reasons = W.attention_reasons(
        {"is_repo": True, "has_commits": True, "uncommitted": 0, "age_days": 120.0}
    )
    assert reasons == ["no commit in 120 days"]


def test_a_settled_workspace_asks_for_nothing():
    assert W.attention_reasons(
        {"is_repo": True, "has_commits": True, "uncommitted": 0, "age_days": 6.0}
    ) == []


def test_a_broken_or_missing_one_is_not_listed_as_attention():
    """A workspace whose folder is gone is a thing to fix, not a thing that wants
    your work — and it must not push the count that a person reads."""
    assert W.attention_reasons({"missing": True, "uncommitted": 0}) == []
    assert W.attention_reasons({"problem": "git is not installed"}) == []


def test_nothing_committed_is_a_reason():
    assert W.attention_reasons(
        {"is_repo": True, "has_commits": False, "uncommitted": 0}
    ) == ["nothing committed yet"]


def test_the_summary_counts_what_the_line_claims():
    states = [
        {"uncommitted": 12, "reasons": ["12 uncommitted"], "markers": [1, 2]},
        {"uncommitted": 0, "reasons": [], "markers": []},
        {"uncommitted": 3, "reasons": ["3 uncommitted"], "markers": [1]},
    ]
    summary = W.summarize(states)
    assert summary == {
        "total": 3, "need_attention": 2, "dirty": 2, "broken": 0, "markers": 3,
    }
    assert W.attention_line(summary) == "2 workspaces need attention"


def test_a_folder_that_is_gone_needs_attention_too():
    """Not work waiting, but not settled either: it is unknown, which is worse.

    This is the case that must never be glossed: a dashboard that counted a
    missing folder as settled would reassure someone about a folder it never
    found. It sorts below real work (see `sort_states`), but it counts.
    """
    gone = {"missing": True, "uncommitted": 0, "reasons": [], "markers": []}
    no_git = {"problem": "git did not answer within 5s", "uncommitted": 0,
              "reasons": [], "markers": []}
    settled = {"uncommitted": 0, "reasons": [], "markers": []}
    summary = W.summarize([gone, no_git, settled])
    assert summary["need_attention"] == 2
    assert summary["broken"] == 2
    assert W.attention_line(summary) == "2 workspaces need attention"
    assert W.needs_attention(gone) and W.needs_attention(no_git)
    assert not W.needs_attention(settled)


def test_the_summary_line_is_honest_when_there_is_nothing_to_report():
    assert W.attention_line(W.summarize([])) == "no workspaces yet"
    assert W.attention_line(
        W.summarize([{"uncommitted": 0, "reasons": [], "markers": []}])
    ) == "all 1 workspaces are settled"
    assert W.summarize([])["total"] == 0


# ---------------------------------------------- opening a place in a folder


def _tree(tmp_path):
    """A small real tree: something inside the folder, something outside it."""
    root = tmp_path / "project"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("x = 1\n")
    (root / "README.md").write_text("hi\n")
    outside = tmp_path / "elsewhere.py"
    outside.write_text("nope\n")
    return root, outside


def test_a_real_file_inside_the_folder_is_the_only_thing_openable(tmp_path):
    root, _ = _tree(tmp_path)
    assert W.inside_folder(str(root), "app/main.py") == str(root / "app" / "main.py")
    assert W.inside_folder(str(root), "README.md") == str(root / "README.md")
    # A trailing newline or space is somebody's copy-paste, not a different file.
    assert W.inside_folder(str(root), " README.md ") == str(root / "README.md")


def test_a_path_out_of_the_folder_is_refused_rather_than_sanitized(tmp_path):
    """The load-bearing one.

    `file` arrives from a request, which means it arrives from a card, which
    means it arrives from whatever is in the request. The app is not a file
    browser: the only thing it will open is a real file inside this folder, and
    each of these is refused outright -- not trimmed into something openable,
    because a sanitized path is still a request choosing a file.
    """
    root, outside = _tree(tmp_path)
    for attempt in [
        "../elsewhere.py",
        "../../etc/passwd",
        "/etc/passwd",
        str(outside),               # absolute, real, and not ours
        "app/../../elsewhere.py",
        "app/../../../etc/hosts",
        "~/.ssh/id_rsa",
    ]:
        assert W.inside_folder(str(root), attempt) is None, attempt


def test_an_option_flag_is_not_a_file(tmp_path):
    root, _ = _tree(tmp_path)
    # `--wait` is an argument, not a path, and naming it as a "file" is the one
    # way a validated-looking value still becomes a flag on the argv.
    assert W.inside_folder(str(root), "--wait") is None
    assert W.inside_folder(str(root), "-g") is None
    assert W.inside_folder(str(root), " --goto") is None


def test_a_sibling_folder_sharing_the_prefix_is_not_inside(tmp_path):
    """`startswith(root)` on its own lets `project-notes` through; the separator
    is what makes "inside" mean inside."""
    root, _ = _tree(tmp_path)
    sibling = tmp_path / "project-notes"
    sibling.mkdir()
    (sibling / "secret.py").write_text("x\n")
    assert W.inside_folder(str(root), "../project-notes/secret.py") is None


def test_a_folder_is_not_a_file_to_open(tmp_path):
    root, _ = _tree(tmp_path)
    for attempt in ["app", ".", "", "   ", None, 12, {"file": "x"}]:
        assert W.inside_folder(str(root), attempt) is None, attempt


def test_the_editor_is_told_where_to_put_the_cursor():
    assert W.editor_args("/usr/bin/code", "/tmp/p", file="/tmp/p/a.py", line=12) == [
        "/usr/bin/code", "--goto", "/tmp/p/a.py:12",
    ]
    assert W.editor_args("/usr/bin/code", "/tmp/p", file="/tmp/p/a.py") == [
        "/usr/bin/code", "--goto", "/tmp/p/a.py",
    ]
    assert W.editor_args("/usr/bin/code", "/tmp/p") == ["/usr/bin/code", "/tmp/p"]


def test_a_terminal_takes_the_folder_and_ignores_the_file():
    """A file name means nothing to a terminal. It is ignored rather than
    refused, and the argv the server reports is what keeps that from being a
    silent difference between what was asked and what ran."""
    assert W.open_args("terminal", "/usr/bin/alacritty", "/tmp/p",
                       file="/tmp/p/a.py", line=3) == [
        "/usr/bin/alacritty", "--working-directory", "/tmp/p",
    ]
    assert W.open_args("files", "/usr/bin/xdg-open", "/tmp/p", file="/tmp/p/a.py") == [
        "/usr/bin/xdg-open", "/tmp/p/a.py",
    ]
    assert W.open_args("files", "/usr/bin/xdg-open", "/tmp/p") == [
        "/usr/bin/xdg-open", "/tmp/p",
    ]


# ------------------------------------------------------------- opening a folder


def test_each_terminal_is_told_the_directory_its_own_way():
    assert W.terminal_args("/usr/bin/alacritty", "/tmp/x") == [
        "/usr/bin/alacritty", "--working-directory", "/tmp/x",
    ]
    assert W.terminal_args("/usr/bin/mate-terminal", "/tmp/x") == [
        "/usr/bin/mate-terminal", "--working-directory=/tmp/x",
    ]
    # xterm has no such flag, so it has to be told to cd first
    argv = W.terminal_args("/usr/bin/xterm", "/tmp/x")
    assert argv[:3] == ["/usr/bin/xterm", "-e", "sh"]
    assert "cd" in argv[-1] and "/tmp/x" in argv[-1]


def test_the_editor_and_the_file_manager_just_get_the_path():
    assert W.editor_args("/usr/bin/code", "/tmp/x") == ["/usr/bin/code", "/tmp/x"]
    assert W.file_args("/usr/bin/xdg-open", "/tmp/x") == ["/usr/bin/xdg-open", "/tmp/x"]


def test_an_action_that_is_not_one_of_the_three_is_refused():
    with pytest.raises(ValueError):
        W.open_args("rm -rf", "/usr/bin/sh", "/tmp/x")
    with pytest.raises(ValueError):
        W.candidates_for("shell")


def test_a_missing_tool_names_what_it_looked_for():
    assert "no file manager found" in W.missing_tool_words("files")
    assert "alacritty" in W.missing_tool_words("terminal")
    assert "code" in W.missing_tool_words("editor")


def test_a_folder_inside_a_repo_reports_only_its_own_prefix():
    assert W.prefix_for("/r/sub/dir", "/r") == "sub/dir/"
    assert W.prefix_for("/r", "/r") == ""
    entries = ["sub/dir/a.py", "sub/dir/b/c.py", "sub/other.py", "top.py"]
    assert W.entries_under(entries, "sub/dir/") == ["sub/dir/a.py", "sub/dir/b/c.py"]
    assert W.entries_under(entries, "") == entries


# ------------------------------------------------------- reading a real folder


def run(*argv, cwd):
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real git repo, because the thing under test parses real git."""
    run("git", "init", "-q", "-b", "main", cwd=tmp_path)
    run("git", "config", "user.email", "t@example.com", cwd=tmp_path)
    run("git", "config", "user.name", "Tester", cwd=tmp_path)
    (tmp_path / "app.py").write_text("print('hello')\n")
    run("git", "add", "-A", cwd=tmp_path)
    run("git", "commit", "-qm", "first", cwd=tmp_path)
    return tmp_path


def test_a_real_repo_reads_as_a_repo(repo):
    state = R.read_path(str(repo), now=NOW)
    assert state["is_repo"] is True
    assert state["nested"] is False
    assert state["branch"] == "main"
    assert state["has_commits"] is True
    assert state["uncommitted"] == 0
    assert state["last"]["subject"] == "first"


def test_a_real_uncommitted_change_shows_up(repo):
    (repo / "app.py").write_text("print('changed')\n")
    (repo / "new.py").write_text("# TODO: a real marker in a real file\n")
    state = R.read_path(str(repo), now=NOW)
    assert state["uncommitted"] == 2
    assert "app.py" in state["changed"]
    assert "new.py" in state["untracked"]
    assert state["markers"] == [
        {"file": "new.py", "line": 1, "kind": "TODO", "text": "a real marker in a real file"}
    ]
    assert state["reasons"] == ["2 uncommitted"]
    assert state["words"].startswith("2 uncommitted")


def test_a_real_folder_that_is_not_a_repo_is_still_read(tmp_path_factory):
    """`tmp_path` is the repo fixture's own directory, so a folder made inside it
    is inside a repo -- which is the *nested* case, not this one."""
    plain = tmp_path_factory.mktemp("plain") / "scripts"
    plain.mkdir(parents=True)
    (plain / "run.sh").write_text("#!/bin/sh\n# TODO: make this executable\n")
    state = R.read_path(str(plain), now=NOW)
    assert state["is_repo"] is False
    assert state["words"] == "1 files, not a git repo"
    assert [m["file"] for m in state["markers"]] == ["run.sh"]


def test_a_real_folder_inside_a_repo_counts_only_itself(repo):
    inner = repo / "part"
    inner.mkdir()
    (inner / "mine.py").write_text("x = 1\n")
    (repo / "elsewhere.py").write_text("y = 2\n")
    state = R.read_path(str(inner), now=NOW)
    assert state["nested"] is True
    assert state["repo_name"] == repo.name
    # `--untracked-files=normal` collapses an untracked *directory* into one
    # entry, so git says "part/" rather than naming the file inside it. The
    # count is git's, and the view shows what git said.
    assert state["uncommitted"] == 1
    assert state["changed"] + state["untracked"] == ["part/"]
    assert state["repo_uncommitted"] == 2
    assert "elsewhere.py" not in state["changed"] + state["untracked"]


def test_a_path_that_is_not_there_says_so_and_does_not_raise(tmp_path):
    state = R.read_path(str(tmp_path / "gone"), now=NOW)
    assert state["missing"] is True
    assert state["words"] == "that folder is gone"
    assert state["markers"] == []


def test_a_real_repo_with_no_commit_yet(tmp_path):
    run("git", "init", "-q", "-b", "main", cwd=tmp_path)
    (tmp_path / "x.py").write_text("x = 1\n")
    state = R.read_path(str(tmp_path), now=NOW)
    assert state["is_repo"] is True
    assert state["has_commits"] is False
    assert state["words"] == "no commits yet, 1 waiting"


def test_a_real_detached_head_does_not_invent_a_branch(repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    run("git", "checkout", "-q", head, cwd=repo)
    state = R.read_path(str(repo), now=NOW)
    assert state["detached"] is True
    assert state["branch"] is None
    assert state["has_commits"] is True


def test_a_real_rename_is_reported_at_its_new_path(repo):
    run("git", "mv", "app.py", "renamed.py", cwd=repo)
    run("git", "commit", "-qm", "move it", cwd=repo)
    state = R.read_path(str(repo), now=NOW)
    assert state["uncommitted"] == 0
    assert state["last"]["subject"] == "move it"


def test_walking_a_repo_leaves_dependencies_alone(repo):
    noise = repo / "node_modules" / "pkg"
    noise.mkdir(parents=True)
    (noise / "index.js").write_text("// TODO: not my code\n")
    (repo / "real.py").write_text("# TODO: my code\n")
    state = R.read_path(str(repo), now=NOW)
    assert [m["text"] for m in state["markers"]] == ["my code"]


def test_tools_are_reported_per_action():
    found = R.tools()
    assert set(found) == set(W.OPEN_ACTIONS)
    for what, binary in found.items():
        assert binary == "" or os.path.isabs(binary)


def test_opening_a_folder_that_is_gone_is_refused_with_a_reason(tmp_path):
    result = R.open_workspace(str(tmp_path / "gone"), "editor")
    assert result["ok"] is False
    assert result["error"] == "that folder is gone"


def test_opening_an_unknown_action_is_refused():
    result = R.open_workspace("/tmp", "run-anything")
    assert result["ok"] is False
    assert "cannot open" in result["error"]
