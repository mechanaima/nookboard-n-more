"""TDD: SQLite index layer."""
from pathlib import Path
from datetime import date
from app.models import Note, Signifier, Status
from app.db import Database


def test_db_upsert_and_get(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    n = Note(
        id="x1", collection="inbox", title="Buy oat milk",
        body="oat if they have it",
        signifier=Signifier.TASK, status=Status.OPEN,
        dates=[date(2026, 9, 23)],
    )
    db.upsert(n)
    loaded = db.get("x1")
    assert loaded is not None
    assert loaded.title == "Buy oat milk"
    assert loaded.dates == [date(2026, 9, 23)]


def test_db_search_simple(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    for n in [
        Note(id="a", collection="inbox", title="buy milk", body="oat",
             signifier=Signifier.TASK, status=Status.OPEN, dates=[date(2026, 9, 23)]),
        Note(id="b", collection="inbox", title="walk dog", body="morning",
             signifier=Signifier.TASK, status=Status.OPEN, dates=[date(2026, 9, 23)]),
        Note(id="c", collection="home", title="fix sink", body="drip drip",
             signifier=Signifier.NOTE, status=Status.OPEN, dates=[date(2026, 9, 24)]),
    ]:
        db.upsert(n)
    hits = db.search("milk")
    assert {h.id for h in hits} == {"a"}
    hits = db.search("drip")
    assert {h.id for h in hits} == {"c"}


def test_db_search_empty_returns_empty(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    db.upsert(Note(id="x", collection="inbox", title="hello", body="",
                   signifier=Signifier.NOTE, status=Status.OPEN, dates=[]))
    assert db.search("") == []
    assert db.search("   ") == []


def test_db_delete(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    n = Note(id="d1", collection="inbox", title="doomed", body="",
             signifier=Signifier.NOTE, status=Status.OPEN, dates=[])
    db.upsert(n)
    assert db.get("d1") is not None
    db.delete("d1")
    assert db.get("d1") is None


def test_db_month_counts(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    db.upsert(Note(id="a", collection="inbox", title="a", body="",
                   signifier=Signifier.NOTE, status=Status.OPEN,
                   dates=[date(2026, 9, 1), date(2026, 9, 23)]))
    db.upsert(Note(id="b", collection="inbox", title="b", body="",
                   signifier=Signifier.NOTE, status=Status.OPEN,
                   dates=[date(2026, 9, 23)]))
    db.upsert(Note(id="c", collection="inbox", title="c", body="",
                   signifier=Signifier.NOTE, status=Status.OPEN,
                   dates=[date(2026, 10, 1)]))
    counts = db.month_counts(2026, 9)
    assert counts["2026-09-01"] == 1
    assert counts["2026-09-23"] == 2
    assert "2026-10-01" not in counts