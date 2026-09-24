"""TDD: Vault reads/writes Markdown notes on disk."""
from dataclasses import replace
from pathlib import Path
from datetime import date
from app.models import Note, Signifier, Status
from app.vault import Vault


def test_changing_a_collection_moves_the_file(tmp_path: Path):
    """A note's collection is its folder, so changing it has to move the file.

    Without the move the frontmatter says one thing and the folder another, and
    the folder is believed on the next read -- so the change silently reverts and
    the collection dropdown looks broken.
    """
    v = Vault(tmp_path)
    v.write(Note(id="n1", collection="inbox", title="A shape", body="x"))

    # Note is frozen, so a change is a new value -- which is also what the API
    # path does when it rebuilds a note from a PATCH.
    v.write(replace(v.read("n1"), collection="templates"))

    assert (tmp_path / "templates" / "n1.md").exists()
    assert not (tmp_path / "inbox" / "n1.md").exists(), "the old file is gone, not duplicated"
    assert v.read("n1").collection == "templates"
    assert len(v.list_all()) == 1, "one note, not two"


def test_a_note_at_the_vault_root_keeps_its_frontmatter_collection(tmp_path: Path):
    """No folder to defer to, so nothing to move and nothing to reconcile."""
    (tmp_path / "loose.md").write_text(
        "---\nid: loose\ncollection: inbox\ntitle: Loose\n---\n\nbody\n"
    )
    v = Vault(tmp_path)
    v.write(replace(v.read("loose"), collection="templates"))

    assert (tmp_path / "loose.md").exists()
    assert not (tmp_path / "templates" / "loose.md").exists()
    assert v.read("loose").collection == "templates"


def test_vault_round_trip(tmp_path: Path):
    v = Vault(tmp_path)
    n = Note(
        id="abc-123",
        collection="inbox",
        title="Buy milk",
        body="oat milk if they have it",
        signifier=Signifier.TASK,
        status=Status.OPEN,
        dates=[date(2026, 9, 23)],
    )
    v.write(n)

    # File on disk
    on_disk = tmp_path / "inbox" / "abc-123.md"
    assert on_disk.exists()
    assert "Buy milk" in on_disk.read_text()

    # Read back
    loaded = v.read("abc-123")
    assert loaded.id == n.id
    assert loaded.title == n.title
    assert loaded.signifier == Signifier.TASK
    assert loaded.dates == [date(2026, 9, 23)]