"""TDD: Vault reads/writes Markdown notes on disk."""
from pathlib import Path
from datetime import date
from app.models import Note, Signifier, Status
from app.vault import Vault


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