"""TDD: note tags round-trip + filter."""
from datetime import date
from app.models import Note, Signifier, Status
from app.db import Database


def test_note_round_trip_with_tags():
    n = Note(
        id="t1", collection="inbox", title="x", body="",
        signifier=Signifier.TASK, status=Status.OPEN,
        dates=[date(2026, 9, 23)],
        tags=["urgent", "home"],
    )
    md = n.to_markdown()
    parsed = Note.from_markdown(md)
    assert parsed.tags == ["urgent", "home"]


def test_note_tags_default_empty():
    n = Note(id="t2", collection="inbox", title="x", body="",
             signifier=Signifier.TASK, status=Status.OPEN, dates=[])
    md = n.to_markdown()
    parsed = Note.from_markdown(md)
    assert parsed.tags == []


def test_db_tags_persisted(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    n = Note(id="t2", collection="inbox", title="x", body="",
             signifier=Signifier.TASK, status=Status.OPEN,
             dates=[], tags=["work"])
    db.upsert(n)
    row = db.get("t2")
    assert row.tags == ["work"]


def test_db_filter_by_tag(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(Note(id="a", collection="inbox", title="a", body="",
                   signifier=Signifier.TASK, status=Status.OPEN, dates=[],
                   tags=["urgent"]))
    db.upsert(Note(id="b", collection="inbox", title="b", body="",
                   signifier=Signifier.TASK, status=Status.OPEN, dates=[],
                   tags=["home"]))
    db.upsert(Note(id="c", collection="inbox", title="c", body="",
                   signifier=Signifier.TASK, status=Status.OPEN, dates=[],
                   tags=["urgent", "home"]))
    hits = db.filter_by_tag("urgent")
    assert {n.id for n in hits} == {"a", "c"}
    hits = db.filter_by_tag("home")
    assert {n.id for n in hits} == {"b", "c"}