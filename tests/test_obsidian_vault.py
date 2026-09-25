"""Tests for the secondary Obsidian vault reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.obsidian_vault import ObsidianVault


def _extract_folders(note_ids: list[str]) -> list[str]:
    """Extract unique top-level folder prefixes from note ids (client-side logic)."""
    return sorted({nid.split("/")[0] for nid in note_ids if "/" in nid})


class TestExtractFolders:
    def test_empty(self):
        assert _extract_folders([]) == []

    def test_single_folder(self):
        assert _extract_folders(["Co-op/2026-09-24-note", "Co-op/2026-09-23-note"]) == ["Co-op"]

    def test_multiple_folders(self):
        ids = [
            "Co-op/2026-09-24-note",
            "Programming/2026-09-24-note",
            "INFO2380/2026-09-24-note",
        ]
        assert _extract_folders(ids) == ["Co-op", "INFO2380", "Programming"]

    def test_notes_without_folder_are_excluded(self):
        assert _extract_folders(["a-standalone-note", "Co-op/2026-09-24-note"]) == ["Co-op"]

    def test_deduplicates(self):
        ids = ["Co-op/2026-09-24-a", "Co-op/2026-09-25-b", "Programming/2026-09-24-c"]
        assert _extract_folders(ids) == ["Co-op", "Programming"]


class TestObsidianUrl:
    """obsidian:// URL construction."""

    def test_plain_note(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path)
        (tmp_path / "My Note.md").write_text("")
        notes = vault.recent_notes(limit=1)
        assert len(notes) == 1
        assert notes[0].id == "My Note"
        assert notes[0].excerpt == ""
        assert "vault=" in notes[0].obsidian_url

    def test_nested_note(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path)
        sub = tmp_path / "subfolder"
        sub.mkdir()
        (sub / "Nested Note.md").write_text("")
        notes = vault.recent_notes(limit=1)
        assert notes[0].id == "subfolder/Nested Note"

    def test_frontmatter_title(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path)
        (tmp_path / "index.md").write_text("---\ntitle: My Custom Title\n---\nBody.")
        notes = vault.recent_notes(limit=1)
        assert notes[0].title == "My Custom Title"

    def test_excerpt_extraction(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path)
        (tmp_path / "note.md").write_text(
            "---\ntitle: Test\n---\n"
            "This is the first line of content.\n"
            "And this is the second line."
        )
        notes = vault.recent_notes(limit=1)
        ex = notes[0].excerpt
        assert "first line" in ex
        assert "second line" in ex
        assert "title:" not in ex        # frontmatter stripped
        assert "---\n" not in ex

    def test_filename_stem_fallback(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path)
        (tmp_path / "just-a-note.md").write_text("No frontmatter title here")
        notes = vault.recent_notes(limit=1)
        assert notes[0].title == "just-a-note"

    def test_url_encodes_vault_name(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path)
        (tmp_path / "note.md").write_text("")
        url = vault.recent_notes(limit=1)[0].obsidian_url
        # The vault name (the tmp_path directory name) should be percent-encoded
        # in the vault param.
        assert "vault=" in url

    def test_skips_dot_folders(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path)
        dot = tmp_path / ".obsidian"
        dot.mkdir()
        (dot / "config.md").write_text("")
        (tmp_path / "real-note.md").write_text("")
        notes = vault.recent_notes(limit=10)
        ids = [n.id for n in notes]
        assert ".obsidian" not in str(ids)
        assert "real-note" in ids

    def test_disabled_vault_returns_empty(self):
        vault = ObsidianVault.disabled()
        assert vault.is_enabled() is False
        assert vault.recent_notes(limit=5) == []

    def test_nonexistent_path_returns_empty(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path / "does-not-exist")
        assert vault.is_enabled() is False
        assert vault.recent_notes(limit=5) == []

    def test_sort_newest_first(self, tmp_path: Path):
        vault = ObsidianVault(root=tmp_path)
        # Create two notes; touch one to make it newer.
        old = tmp_path / "old-note.md"
        new = tmp_path / "new-note.md"
        old.write_text("")
        new.write_text("")
        import time
        time.sleep(0.05)
        new.touch()
        notes = vault.recent_notes(limit=2)
        assert notes[0].id == "new-note"
        assert notes[1].id == "old-note"
