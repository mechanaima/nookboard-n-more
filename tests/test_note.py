"""TDD: define the Note data model."""
from datetime import date
from app.models import Note, Signifier, Status


def test_note_round_trip_to_markdown():
    n = Note(
        id="2026-09-23-clean-kitchen",
        collection="home",
        title="Clean kitchen",
        body="Wipe counters, empty bin.",
        signifier=Signifier.TASK,
        status=Status.OPEN,
        dates=[date(2026, 9, 23)],
        parent_id=None,
        created=date(2026, 9, 23),
    )
    md = n.to_markdown()
    parsed = Note.from_markdown(md)
    assert parsed == n