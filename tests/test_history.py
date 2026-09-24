"""History: what it means (pure) and what it does to a real repository.

Most of these run git for real in a `tmp_path` repository, because the thing
worth testing is the part that touches a disk: whether a commit records the file
that actually changed, whether a restore is itself recorded, and whether the
app's own author name shows up in a log a person will read.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import pytest

from app import history as H
from app import history_run as R

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def repo(tmp_path):
    """A vault-shaped directory with history turned on."""
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "soil.md").write_text("---\ntitle: Soil mix\n---\n\none part peat\n")
    result = R.ensure_repo(str(root))
    assert result["ok"], result
    return root


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


# ------------------------------------------------------- the words (no disk)

def test_a_change_is_named_by_what_it_did():
    assert H.commit_message("new", "Soil mix") == "new: Soil mix"
    assert H.commit_message("edit", "Soil mix") == "edit: Soil mix"
    assert H.commit_message("delete", "Soil mix") == "delete: Soil mix"
    assert H.commit_message("restore", "Soil mix") == "restore: Soil mix"
    # An act this app does not know is an edit: the commit still says something
    # true rather than something invented.
    assert H.commit_message("frobnicate", "Soil mix") == "edit: Soil mix"


def test_a_title_is_one_short_line_in_the_log():
    assert H.commit_message("edit", "  spaced   out  ") == "edit: spaced out"
    assert H.commit_message("edit", "a\nb") == "edit: a b"
    # An untitled note still gets a message, because "edit: " reads as a bug.
    assert H.commit_message("edit", "   ") == "edit: untitled"
    long = H.commit_message("edit", "x" * 300)
    assert long.startswith("edit: xxx") and long.endswith("…")
    assert len(long) <= len("edit: ") + 72


def test_a_log_is_read_by_the_format_that_wrote_it():
    # A subject is arbitrary text: it may contain a bar, a colon, an em dash.
    text = (
        H.FIELD_SEP.join(["a" * 40, "aaaaaaa", "2026-09-24T10:00:00-04:00", "edit: a | b: c"])
        + H.RECORD_SEP
        + H.FIELD_SEP.join(["b" * 40, "bbbbbbb", "2026-09-23T10:00:00-04:00", "new: Soil mix"])
        + H.RECORD_SEP
    )
    entries = H.parse_log(text)
    assert [e["short"] for e in entries] == ["aaaaaaa", "bbbbbbb"]
    assert entries[0]["subject"] == "edit: a | b: c"
    assert entries[1]["when"] == "2026-09-23T10:00:00-04:00"


def test_a_log_that_is_not_one_reads_as_nothing():
    assert H.parse_log("") == []
    assert H.parse_log("\n\n") == []
    assert H.parse_log("fatal: not a git repository") == []


def test_a_change_from_today_can_say_the_clock():
    """A column of rows that all say "just now" says nothing. The clock is what
    separates this morning's work from the ten minutes before now."""
    assert H.clock_label("2026-09-24T15:07:00-04:00") == "15:07"
    # its own offset, not converted to the reader's: the time it was recorded
    assert H.clock_label("2026-09-24T15:07:00+00:00") == "15:07"
    assert H.clock_label("nonsense") == ""
    assert H.clock_label("") == ""
    assert H.clock_label(None) == ""


def test_a_version_says_when_in_words_a_person_uses():
    assert H.version_words({"when": "2026-09-24T10:00:00+00:00"}, NOW) == "2 hours ago"
    # An unreadable date is not silently "just now": it is not a date, so the
    # sentence drops to what is true.
    assert H.version_words({"when": "yesterday-ish"}, NOW) == "at some point"
    assert H.version_words({}, NOW) == "at some point"


# ---------------------------------------------- a path from a request (no disk)

def test_a_path_that_leaves_the_vault_is_refused(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    for attempt in ["../outside.md", "../../etc/passwd", "/etc/passwd", "---force", "", "  ", None, 12]:
        assert H.safe_relpath(str(root), attempt) is None, attempt
    assert H.safe_relpath(str(root), "notes/soil.md") == "notes/soil.md"


def test_a_path_is_allowed_to_name_a_file_that_is_not_there_yet(tmp_path):
    """The whole point of this one: what you restore is usually a note that was
    deleted, so a rule that required the file to exist would refuse exactly the
    case it exists for."""
    root = tmp_path / "vault"
    root.mkdir()
    assert H.safe_relpath(str(root), "gone/vanished.md") == "gone/vanished.md"


def test_a_path_that_wanders_out_and_back_is_still_inside(tmp_path):
    """`a/../b.md` is `b.md`. Resolved and then checked, not rejected for
    looking suspicious -- the check is about where it lands."""
    root = tmp_path / "vault"
    root.mkdir()
    assert H.safe_relpath(str(root), "notes/../soil.md") == "soil.md"
    assert H.safe_relpath(str(root), "./notes/soil.md") == "notes/soil.md"


# ------------------------------------------------------------ turning it on

def test_turning_history_on_commits_the_vault_as_it_stands(tmp_path):
    """Without a baseline, the first edit would be the first commit -- which
    would leave the state you are in right now as the one version with no way
    back to."""
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "soil.md").write_text("one part peat\n")
    result = R.ensure_repo(str(root))
    assert result["ok"] and result["created"] == ["repository", ".gitignore"]
    assert result["committed"]
    assert git(root, "log", "--format=%s").strip() == R.FIRST_COMMIT
    # authored by the app, and it says so
    assert git(root, "log", "--format=%an <%ae>").strip() == "nookboard <nookboard@localhost>"
    assert (root / "notes" / "soil.md").exists()


def test_the_index_is_not_history(tmp_path):
    """The index is markdown that is already there, rebuilt by
    `POST /api/rebuild-index`: committing it would version a cache beside its
    source and make every save look like two changes."""
    root = repo(tmp_path)
    (root / ".index.sqlite").write_text("binary-ish\n")
    (root / "notes" / "other.md").write_text("---\ntitle: Other\n---\n\nhi\n")
    result = R.ensure_repo(str(root))
    assert result["ok"]
    tracked = git(root, "ls-files").split()
    assert "notes/other.md" in tracked
    assert ".index.sqlite" not in tracked


def test_turning_it_on_twice_changes_nothing(tmp_path):
    root = repo(tmp_path)
    before = git(root, "rev-parse", "HEAD").strip()
    second = R.ensure_repo(str(root))
    assert second["ok"] and second["created"] == [] and second["committed"] is None
    assert second["why"] == "nothing has changed since the last commit"
    assert git(root, "rev-parse", "HEAD").strip() == before


def test_a_vault_that_is_not_a_repository_says_so_rather_than_committing(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.md").write_text("x\n")
    result = R.commit_change(str(root), ["a.md"], "edit", "A")
    assert result["ok"] is False and result["off"] is True
    assert "history is not on" in result["why"]
    assert R.is_repo(str(root)) is False


# ------------------------------------------------------------- saving a change

def test_a_save_is_recorded_as_a_change_to_that_note(tmp_path):
    root = repo(tmp_path)
    (root / "notes" / "soil.md").write_text("---\ntitle: Soil mix\n---\n\ntwo parts peat\n")
    result = R.commit_change(str(root), ["notes/soil.md"], "edit", "Soil mix")
    assert result["ok"] and result["committed"]
    assert git(root, "log", "-n1", "--format=%s").strip() == "edit: Soil mix"


def test_a_save_that_changed_nothing_is_not_a_commit(tmp_path):
    """An empty commit per keystroke is a log nobody reads, which is the same as
    no history."""
    root = repo(tmp_path)
    (root / "notes" / "soil.md").write_text("---\ntitle: Soil mix\n---\n\none part peat\n")
    before = git(root, "rev-list", "--count", "HEAD").strip()
    result = R.commit_change(str(root), ["notes/soil.md"], "edit", "Soil mix")
    assert result["ok"] and result["committed"] is None
    assert result["why"] == "nothing changed"
    assert git(root, "rev-list", "--count", "HEAD").strip() == before


def test_a_note_that_moved_records_both_halves(tmp_path):
    """A collection is a folder, so changing a note's collection moves its file.
    Staging only the new path leaves the old one alive in the index and the
    history shows the note existing twice."""
    root = repo(tmp_path)
    (root / "archive").mkdir()
    (root / "archive" / "soil.md").write_text("---\ntitle: Soil mix\n---\n\none part peat\n")
    (root / "notes" / "soil.md").unlink()
    result = R.commit_change(str(root), ["notes/soil.md", "archive/soil.md"], "edit", "Soil mix")
    assert result["ok"]
    # git may call this a rename (same text, moved) rather than a delete and an
    # add. Either way both halves are in the commit, which is the point: the old
    # path must not survive in the index.
    changed = git(root, "show", "--name-status", "--format=", "HEAD")
    assert "notes/soil.md" in changed and "archive/soil.md" in changed
    assert changed.split()[0].startswith(("D", "R"))
    assert not (root / "notes" / "soil.md").exists()
    assert "notes/soil.md" not in git(root, "ls-files").split()


def test_a_path_from_outside_is_not_recorded(tmp_path):
    root = repo(tmp_path)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("not yours\n")
    result = R.commit_change(str(root), ["../elsewhere.md"], "edit", "Elsewhere")
    assert result["ok"] is False
    assert "no path inside the vault" in result["why"]
    assert git(root, "log", "-n1", "--format=%s").strip() == R.FIRST_COMMIT


# -------------------------------------------------------------- reading it back

def test_one_notes_history_is_that_notes_history(tmp_path):
    root = repo(tmp_path)
    (root / "notes" / "other.md").write_text("---\ntitle: Other\n---\n\nhi\n")
    R.commit_change(str(root), ["notes/other.md"], "new", "Other")
    (root / "notes" / "soil.md").write_text("---\ntitle: Soil mix\n---\n\nmore peat\n")
    R.commit_change(str(root), ["notes/soil.md"], "edit", "Soil mix")

    mine = R.log_for(str(root), "notes/soil.md")
    assert [e["subject"] for e in mine] == ["edit: Soil mix", R.FIRST_COMMIT]
    everything = R.log_for(str(root))
    assert [e["subject"] for e in everything][0] == "edit: Soil mix"
    assert "new: Other" in [e["subject"] for e in everything]


def test_the_history_of_a_note_that_is_gone_is_still_there(tmp_path):
    """This is the case history exists for. `git log -- <path>` follows a path
    the working tree no longer has."""
    root = repo(tmp_path)
    (root / "notes" / "soil.md").unlink()
    R.commit_change(str(root), ["notes/soil.md"], "delete", "Soil mix")
    subjects = [e["subject"] for e in R.log_for(str(root), "notes/soil.md")]
    assert subjects[0] == "delete: Soil mix"


def test_a_deleted_note_can_be_found_with_the_version_to_restore_from(tmp_path):
    root = repo(tmp_path)
    (root / "notes" / "soil.md").unlink()
    R.commit_change(str(root), ["notes/soil.md"], "delete", "Soil mix")
    gone = R.deletions(str(root))
    assert [g["path"] for g in gone] == ["notes/soil.md"]
    assert gone[0]["subject"] == "delete: Soil mix"
    # the version to bring back is the deletion's parent, and it is a real sha
    assert R.REV_RE.match(gone[0]["restore_from"])
    text, problem = R.read_version(str(root), gone[0]["restore_from"], "notes/soil.md")
    assert text and "one part peat" in text


def test_a_version_is_read_only_from_a_real_revision(tmp_path):
    root = repo(tmp_path)
    sha = git(root, "rev-parse", "HEAD").strip()
    text, problem = R.read_version(str(root), sha, "notes/soil.md")
    assert text and "one part peat" in text and problem == ""
    # Revision *syntax* is not this app's business: it has shas.
    for attempt in ["HEAD~1", "main", "HEAD", "abc", "", "  "]:
        text, problem = R.read_version(str(root), attempt, "notes/soil.md")
        assert text is None and "not a version" in problem, attempt
    text, problem = R.read_version(str(root), sha, "../outside.md")
    assert text is None and "not inside the vault" in problem


# ------------------------------------------------------------------ undoing

def test_restoring_a_version_writes_the_old_text_back(tmp_path):
    root = repo(tmp_path)
    before = git(root, "rev-parse", "HEAD").strip()
    (root / "notes" / "soil.md").write_text("---\ntitle: Soil mix\n---\n\nWRECKED\n")
    R.commit_change(str(root), ["notes/soil.md"], "edit", "Soil mix")

    result = R.restore_version(str(root), "notes/soil.md", before)
    assert result["ok"] and result["restored"] == before
    assert "one part peat" in (root / "notes" / "soil.md").read_text()
    assert "WRECKED" not in (root / "notes" / "soil.md").read_text()


def test_a_restore_is_itself_recorded(tmp_path):
    """Undo being undoable is the difference between a history you experiment
    with and one you are afraid of."""
    root = repo(tmp_path)
    before = git(root, "rev-parse", "HEAD").strip()
    (root / "notes" / "soil.md").write_text("---\ntitle: Soil mix\n---\n\nWRECKED\n")
    R.commit_change(str(root), ["notes/soil.md"], "edit", "Soil mix")
    R.restore_version(str(root), "notes/soil.md", before)
    assert git(root, "log", "-n1", "--format=%s").strip() == "restore: soil"


def test_a_deleted_note_can_be_brought_back(tmp_path):
    root = repo(tmp_path)
    (root / "notes" / "soil.md").unlink()
    R.commit_change(str(root), ["notes/soil.md"], "delete", "Soil mix")
    gone = R.deletions(str(root))[0]
    result = R.restore_version(str(root), gone["path"], gone["restore_from"])
    assert result["ok"]
    assert (root / "notes" / "soil.md").read_text().startswith("---\ntitle: Soil mix")


def test_a_restore_from_outside_the_vault_is_refused(tmp_path):
    root = repo(tmp_path)
    sha = git(root, "rev-parse", "HEAD").strip()
    result = R.restore_version(str(root), "../elsewhere.md", sha)
    assert result["ok"] is False and "not inside the vault" in result["why"]
    assert not (tmp_path / "elsewhere.md").exists()


@pytest.mark.parametrize("attempt", ["HEAD~1", "../../etc/passwd", "main"])
def test_a_restore_of_something_it_cannot_read_says_why(tmp_path, attempt):
    root = repo(tmp_path)
    result = R.restore_version(str(root), "notes/soil.md", attempt)
    assert result["ok"] is False
    assert "not a version" in result["why"]
