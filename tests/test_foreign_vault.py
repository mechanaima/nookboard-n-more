"""TDD: the vault must not relocate files in a foreign vault.

Pointing nookboard at an Obsidian vault must be non-destructive. Obsidian
files are named after the note and can sit anywhere; nookboard's managed
layout is `<collection>/<id>.md`. If we applied the managed layout on write,
editing a note would move it and break every link pointing at it.
"""
from pathlib import Path

from app.vault import Vault


def _obsidian_vault(root: Path) -> None:
    (root / "Projects").mkdir(parents=True)
    (root / "Daily").mkdir(parents=True)
    (root / "Readme.md").write_text("# Readme\n\ninline tag #meta\n")
    (root / "Projects" / "Roadmap.md").write_text("---\ntags: big\n---\n\nplan\n")
    (root / "Daily" / "2026-09-23.md").write_text("notes for the day #journal\n")


def test_reads_bare_obsidian_notes(tmp_path: Path):
    _obsidian_vault(tmp_path)
    v = Vault(tmp_path)
    ids = {n.id for n in v.list_all()}
    assert ids == {"Readme", "Roadmap", "2026-09-23"}


def test_title_falls_back_to_filename(tmp_path: Path):
    _obsidian_vault(tmp_path)
    v = Vault(tmp_path)
    assert v.read("Roadmap").title == "Roadmap"


def test_nested_folder_becomes_collection(tmp_path: Path):
    _obsidian_vault(tmp_path)
    v = Vault(tmp_path)
    assert v.read("Roadmap").collection == "Projects"
    assert v.read("2026-09-23").collection == "Daily"


def test_inline_tags_are_picked_up_from_a_foreign_vault(tmp_path: Path):
    _obsidian_vault(tmp_path)
    v = Vault(tmp_path)
    assert v.read("Readme").tags == ["meta"]
    assert v.read("2026-09-23").tags == ["journal"]


def test_write_returns_to_the_original_path(tmp_path: Path):
    """The important one: editing must not move the file."""
    _obsidian_vault(tmp_path)
    v = Vault(tmp_path)
    note = v.read("Roadmap")

    from dataclasses import replace
    v.write(replace(note, body="plan v2"))

    assert (tmp_path / "Projects" / "Roadmap.md").exists()
    assert not (tmp_path / "inbox").exists(), "must not create a managed dir"
    assert "plan v2" in (tmp_path / "Projects" / "Roadmap.md").read_text()


def test_write_preserves_a_root_level_obsidian_file(tmp_path: Path):
    _obsidian_vault(tmp_path)
    v = Vault(tmp_path)
    note = v.read("Readme")

    from dataclasses import replace
    v.write(replace(note, body="edited"))

    assert (tmp_path / "Readme.md").exists()
    assert "edited" in (tmp_path / "Readme.md").read_text()


def test_delete_removes_the_original_file(tmp_path: Path):
    _obsidian_vault(tmp_path)
    v = Vault(tmp_path)
    v.delete("Roadmap")
    assert not (tmp_path / "Projects" / "Roadmap.md").exists()
    assert (tmp_path / "Readme.md").exists()


def test_new_notes_still_use_the_managed_layout(tmp_path: Path):
    """Notes nookboard creates itself have no source_rel, so they get <col>/<id>.md."""
    from app.models import Note, Signifier, Status
    v = Vault(tmp_path)
    v.write(Note(id="fresh", collection="home", title="Fresh", body="x",
                 signifier=Signifier.TASK, status=Status.OPEN, dates=[]))
    assert (tmp_path / "home" / "fresh.md").exists()


def test_round_trip_emits_an_alias_for_obsidian(tmp_path: Path):
    _obsidian_vault(tmp_path)
    v = Vault(tmp_path)
    note = v.read("Roadmap")

    from dataclasses import replace
    v.write(replace(note, title="Q4 Roadmap"))

    text = (tmp_path / "Projects" / "Roadmap.md").read_text()
    assert "aliases:" in text
    assert "Q4 Roadmap" in text
