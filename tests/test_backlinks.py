"""TDD: backlinks computed from body wikilinks."""
from app.models import Note, Signifier, Status
from app.db import Database


def test_db_backlinks(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(Note(id="a", collection="inbox", title="Garden tour",
                   body="see [[Buying plants]] for the list",
                   signifier=Signifier.NOTE, status=Status.OPEN, dates=[]))
    db.upsert(Note(id="b", collection="home", title="Buying plants",
                   body="rose, basil, mint",
                   signifier=Signifier.NOTE, status=Status.OPEN, dates=[]))
    back = db.backlinks_for_title("Buying plants")
    assert {n.id for n in back} == {"a"}
    back = db.backlinks_for_title("Garden tour")
    assert back == []  # nobody links TO Garden tour


def test_db_backlinks_multiple_sources(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(Note(id="a", collection="inbox", title="x",
                   body="[[Target]]", signifier=Signifier.NOTE, status=Status.OPEN, dates=[]))
    db.upsert(Note(id="b", collection="inbox", title="y",
                   body="also [[Target]]", signifier=Signifier.NOTE, status=Status.OPEN, dates=[]))
    db.upsert(Note(id="c", collection="inbox", title="z",
                   body="unrelated", signifier=Signifier.NOTE, status=Status.OPEN, dates=[]))
    db.upsert(Note(id="t", collection="inbox", title="Target",
                   body="", signifier=Signifier.NOTE, status=Status.OPEN, dates=[]))
    back = db.backlinks_for_title("Target")
    assert {n.id for n in back} == {"a", "b"}


def test_db_backlinks_updated_on_edit(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    n = Note(id="a", collection="inbox", title="a",
             body="[[Target]]", signifier=Signifier.NOTE, status=Status.OPEN, dates=[])
    db.upsert(n)
    db.upsert(Note(id="t", collection="inbox", title="Target", body="",
                   signifier=Signifier.NOTE, status=Status.OPEN, dates=[]))
    assert {n.id for n in db.backlinks_for_title("Target")} == {"a"}
    # Now remove the link
    n2 = Note(id="a", collection="inbox", title="a", body="no link here",
              signifier=Signifier.NOTE, status=Status.OPEN, dates=[])
    db.upsert(n2)
    assert db.backlinks_for_title("Target") == []