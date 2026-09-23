"""TDD: recurring-note scheduler."""
from datetime import date, timedelta
from app.models import Note, Signifier, Status
from app.db import Database


def test_recurring_weekly_creates_one_instance(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(Note(id="r1", collection="home", title="weekly review",
                   body="checklist", signifier=Signifier.TASK, status=Status.OPEN,
                   dates=[date(2026, 9, 1)],
                   created=date(2026, 9, 1),
                   recurrence="weekly"))
    # 8 days later → 1 instance due
    created = db.run_recurring(date(2026, 9, 9))
    assert len(created) == 1
    assert created[0].title.startswith("weekly review")
    assert created[0].dates == [date(2026, 9, 8)]
    assert created[0].parent_id == "r1"


def test_recurring_idempotent(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(Note(id="r1", collection="home", title="weekly review",
                   body="", signifier=Signifier.TASK, status=Status.OPEN,
                   dates=[date(2026, 9, 1)], created=date(2026, 9, 1),
                   recurrence="weekly"))
    db.run_recurring(date(2026, 9, 9))
    created = db.run_recurring(date(2026, 9, 9))
    assert created == []


def test_recurring_creates_multiple_when_far_past(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(Note(id="d", collection="home", title="daily standup",
                   body="", signifier=Signifier.TASK, status=Status.OPEN,
                   dates=[date(2026, 9, 1)], created=date(2026, 9, 1),
                   recurrence="daily"))
    # From 09-01, daily → 09-02, 09-03, 09-04, 09-05, 09-06 (5 days, all ≤ today)
    created = db.run_recurring(date(2026, 9, 6))
    assert len(created) == 5
    assert [c.dates[0] for c in created] == [
        date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4),
        date(2026, 9, 5), date(2026, 9, 6),
    ]


def test_recurring_does_nothing_in_future(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(Note(id="r", collection="home", title="x", body="",
                   signifier=Signifier.TASK, status=Status.OPEN,
                   dates=[date(2026, 9, 1)], created=date(2026, 9, 1),
                   recurrence="weekly"))
    created = db.run_recurring(date(2026, 9, 1))  # same day as last_run
    assert created == []


def test_recurring_ignores_notes_without_recurrence(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(Note(id="x", collection="home", title="x", body="",
                   signifier=Signifier.TASK, status=Status.OPEN,
                   dates=[date(2026, 9, 1)], created=date(2026, 9, 1),
                   recurrence=None))
    created = db.run_recurring(date(2026, 12, 1))
    assert created == []