"""TDD for mood/pain over time (`app.mood`) and its API.

The behaviour under test is mostly *judgement* — which day a reading belongs
to, and what a day with several readings shows — so these tests pin the
decisions rather than just the plumbing.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.models import Note, coerce_pain, normalize_mood
from app.mood import collapse, daily_series, note_days, readings, streak, summarize

# A fixed "today" so nothing here depends on when the suite runs.
TODAY = date(2026, 9, 23)


def note(nid="n1", *, mood=None, pain=None, dates=(), created=date(2026, 9, 1), title=None):
    return Note(
        id=nid,
        collection="inbox",
        title=title or nid,
        body="",
        dates=list(dates),
        created=created,
        mood=mood,
        pain=pain,
    )


def day(mood=None, pain=None, date_iso="2026-09-01", count=1, **kw):
    """A collapsed day as `daily_series` would emit it."""
    record = {
        "date": date_iso,
        "mood": mood,
        "score": {"great": 5, "good": 4, "meh": 3, "low": 2, "bad": 1}.get(mood),
        "pain": pain,
        "count": count,
        "moods": [mood] if mood else [],
    }
    record.update(kw)
    return record


# --------------------------------------------------------------------------
# Which day does a reading belong to?
# --------------------------------------------------------------------------

def test_undated_note_counts_for_the_day_it_was_captured():
    n = note(created=date(2026, 3, 4))
    assert note_days(n) == [date(2026, 3, 4)]


def test_explicit_dates_win_over_created():
    n = note(dates=[date(2026, 5, 1)], created=date(2026, 9, 1))
    assert note_days(n) == [date(2026, 5, 1)]


def test_a_multi_date_note_belongs_to_every_date_it_carries():
    n = note(dates=[date(2026, 5, 1), date(2026, 5, 3)])
    assert note_days(n) == [date(2026, 5, 1), date(2026, 5, 3)]


def test_duplicate_dates_are_deduped_so_one_note_counts_once_per_day():
    n = note(dates=[date(2026, 5, 1), date(2026, 5, 1)], mood="good")
    assert note_days(n) == [date(2026, 5, 1)]
    assert len(readings([n])[date(2026, 5, 1)]) == 1


# --------------------------------------------------------------------------
# Which notes are readings at all?
# --------------------------------------------------------------------------

def test_notes_with_neither_mood_nor_pain_are_not_readings():
    # Otherwise every note ever written would look like a logged mood day.
    assert readings([note("a"), note("b")]) == {}


def test_a_mood_reading_carries_its_score():
    got = readings([note("a", mood="good")])[date(2026, 9, 1)]
    assert got == [{"id": "a", "title": "a", "mood": "good", "score": 4, "pain": None}]


def test_mood_is_normalised_for_case_and_whitespace():
    got = readings([note("a", mood="  GOOD ")])[date(2026, 9, 1)]
    assert got[0]["mood"] == "good"


def test_an_unrecognised_mood_is_not_plotted():
    # A hand-written or foreign vault may hold anything; it is not an error,
    # it just is not a level we can colour.
    assert readings([note("a", mood="euphoric")]) == {}


def test_an_unrecognised_mood_still_contributes_its_pain():
    # The day has a real pain reading, so it should not vanish just because
    # the mood word was unplottable.
    got = readings([note("a", mood="euphoric", pain=6)])[date(2026, 9, 1)]
    assert got[0]["mood"] is None
    assert got[0]["score"] is None
    assert got[0]["pain"] == 6


def test_a_multi_date_note_reports_the_same_reading_on_each_day():
    n = note("a", mood="low", dates=[date(2026, 5, 1), date(2026, 5, 2)])
    got = readings([n])
    assert got[date(2026, 5, 1)][0]["mood"] == "low"
    assert got[date(2026, 5, 2)][0]["mood"] == "low"


# --------------------------------------------------------------------------
# What does a day with several readings show?
# --------------------------------------------------------------------------

def test_a_day_collapses_to_its_worst_mood_not_its_average():
    # The whole point: a good morning and a bad evening must not average into
    # a bland "meh" that hides the day worth looking back at.
    entries = readings([
        note("a", mood="great", created=date(2026, 9, 1)),
        note("b", mood="bad", created=date(2026, 9, 1)),
    ])[date(2026, 9, 1)]
    assert collapse(entries)["mood"] == "bad"
    assert collapse(entries)["score"] == 1


def test_a_day_collapses_to_its_highest_pain():
    entries = readings([
        note("a", pain=2, created=date(2026, 9, 1)),
        note("b", pain=9, created=date(2026, 9, 1)),
    ])[date(2026, 9, 1)]
    assert collapse(entries)["pain"] == 9


def test_collapse_reports_how_many_readings_and_which_levels():
    entries = readings([
        note("a", mood="good", created=date(2026, 9, 1)),
        note("b", mood="low", created=date(2026, 9, 1)),
        note("c", mood="low", created=date(2026, 9, 1)),
    ])[date(2026, 9, 1)]
    got = collapse(entries)
    assert got["count"] == 3
    # Distinct, and ordered best -> worst so the UI can flag a mixed day.
    assert got["moods"] == ["good", "low"]


def test_a_pain_only_day_has_no_mood():
    entries = readings([note("a", pain=4)])[date(2026, 9, 1)]
    got = collapse(entries)
    assert got["mood"] is None
    assert got["score"] is None
    assert got["pain"] == 4


# --------------------------------------------------------------------------
# The series
# --------------------------------------------------------------------------

def test_series_only_contains_logged_days():
    # Gaps are the caller's to draw: an absent day is not an empty day.
    days = daily_series([note("a", mood="good"), note("b")])
    assert [d["date"] for d in days] == ["2026-09-01"]


def test_series_is_oldest_first():
    days = daily_series([
        note("c", mood="good", created=date(2026, 9, 9)),
        note("a", mood="bad", created=date(2026, 9, 1)),
        note("b", mood="meh", created=date(2026, 9, 5)),
    ])
    assert [d["date"] for d in days] == ["2026-09-01", "2026-09-05", "2026-09-09"]


def test_series_range_is_inclusive_at_both_ends():
    notes = [
        note("a", mood="good", created=date(2026, 9, 1)),
        note("b", mood="good", created=date(2026, 9, 5)),
        note("c", mood="good", created=date(2026, 9, 9)),
    ]
    got = daily_series(notes, start=date(2026, 9, 1), end=date(2026, 9, 5))
    assert [d["date"] for d in got] == ["2026-09-01", "2026-09-05"]


def test_series_carries_the_underlying_notes_so_nothing_is_lost():
    days = daily_series([
        note("b", mood="low", title="Zebra", created=date(2026, 9, 1)),
        note("a", mood="good", title="apple", created=date(2026, 9, 1)),
    ])
    assert [n["title"] for n in days[0]["notes"]] == ["apple", "Zebra"]
    assert days[0]["count"] == 2


# --------------------------------------------------------------------------
# Streaks
# --------------------------------------------------------------------------

def test_no_readings_means_no_streak():
    assert streak([], TODAY) == 0


def test_a_streak_counts_back_from_today():
    days = [
        day("good", date_iso="2026-09-23"),
        day("good", date_iso="2026-09-22"),
        day("good", date_iso="2026-09-21"),
    ]
    assert streak(days, TODAY) == 3


def test_an_unlogged_today_does_not_break_the_streak_yet():
    # The day is not over; reporting 0 here would punish you for opening the
    # app in the morning.
    days = [day("good", date_iso="2026-09-22"), day("good", date_iso="2026-09-21")]
    assert streak(days, TODAY) == 2


def test_a_gap_breaks_the_streak():
    days = [
        day("good", date_iso="2026-09-23"),
        day("good", date_iso="2026-09-22"),
        # 21st missing
        day("good", date_iso="2026-09-20"),
    ]
    assert streak(days, TODAY) == 2


def test_a_streak_that_ended_before_yesterday_is_zero():
    days = [day("good", date_iso="2026-09-10")]
    assert streak(days, TODAY) == 0


def test_pain_only_days_do_not_extend_a_mood_streak():
    days = [
        day("good", date_iso="2026-09-22"),
        day(None, pain=5, date_iso="2026-09-21"),
    ]
    assert streak(days, TODAY) == 1


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

def test_summary_of_nothing_is_all_none_not_zeros():
    got = summarize([], today=TODAY)
    assert got["days_logged"] == 0
    assert got["entries"] == 0
    assert got["streak"] == 0
    assert got["most_common"] is None
    assert got["avg_score"] is None
    assert got["avg_mood"] is None
    assert got["avg_pain"] is None
    assert got["first"] is None and got["last"] is None


def test_summary_counts_each_level():
    got = summarize([
        day("good", date_iso="2026-09-01"),
        day("good", date_iso="2026-09-02"),
        day("bad", date_iso="2026-09-03"),
    ], today=TODAY)
    assert got["counts"]["good"] == 2
    assert got["counts"]["bad"] == 1
    assert got["counts"]["great"] == 0
    assert got["most_common"] == "good"


def test_a_tie_for_most_common_goes_to_the_better_mood():
    # "Most common" is a neutral label; picking the worst on a tie reads as a
    # verdict the data does not support.
    got = summarize([day("bad", date_iso="2026-09-01"), day("good", date_iso="2026-09-02")],
                    today=TODAY)
    assert got["most_common"] == "good"


def test_summary_averages_score_and_names_the_nearest_level():
    got = summarize([
        day("great", date_iso="2026-09-01"),  # 5
        day("meh", date_iso="2026-09-02"),    # 3
    ], today=TODAY)
    assert got["avg_score"] == 4.0
    assert got["avg_mood"] == "good"


def test_summary_average_rounds_half_up_not_to_even():
    # 2.5 must land on "meh" (3). round() is banker's rounding, so it would
    # come out as "low" (2) here but 4 for 3.5 — inconsistent in a way nobody
    # expects from an average.
    got = summarize([
        day("meh", date_iso="2026-09-01"),  # 3
        day("low", date_iso="2026-09-02"),  # 2
    ], today=TODAY)
    assert got["avg_score"] == 2.5
    assert got["avg_mood"] == "meh"


def test_summary_averages_pain_over_pain_days_only():
    got = summarize([
        day("good", pain=2, date_iso="2026-09-01"),
        day("good", pain=4, date_iso="2026-09-02"),
        day("good", date_iso="2026-09-03"),  # no pain reading
    ], today=TODAY)
    assert got["avg_pain"] == 3.0
    assert got["pain_days"] == 2


def test_summary_reports_whether_today_is_logged():
    assert summarize([day("good", date_iso="2026-09-23")], today=TODAY)["logged_today"] is True
    assert summarize([day("good", date_iso="2026-09-22")], today=TODAY)["logged_today"] is False


def test_summary_entries_counts_readings_not_days():
    got = summarize([day("bad", count=3, date_iso="2026-09-01")], today=TODAY)
    assert got["days_logged"] == 1
    assert got["entries"] == 3


def test_summary_spans_first_and_last():
    got = summarize([
        day("good", date_iso="2026-09-01"),
        day("good", date_iso="2026-09-30"),
    ], today=TODAY)
    assert got["first"] == "2026-09-01"
    assert got["last"] == "2026-09-30"


# --------------------------------------------------------------------------
# Coercion
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (0, 0), (10, 10), (7, 7),
    (12, 10),    # clamped, not rejected: "12" on a bad day is real
    (-3, 0),
    ("4.6", 5),
    ("", None), (None, None), ("abc", None), ("", None),
])
def test_coerce_pain_clamps_and_survives_junk(raw, expected):
    assert coerce_pain(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("great", "great"), ("GREAT", "great"), (" bad ", "bad"),
    ("", None), (None, None), ("happy", None), (7, None),
])
def test_normalize_mood(raw, expected):
    assert normalize_mood(raw) == expected


# --------------------------------------------------------------------------
# Model + index round-trip
# --------------------------------------------------------------------------

def test_pain_survives_a_markdown_round_trip():
    n = note("a", mood="low", pain=6)
    assert Note.from_markdown(n.to_markdown(), fallback_id="a").pain == 6


def test_an_unrecognised_mood_survives_a_round_trip():
    # Normalising on read would rewrite a hand-written vault and delete the
    # word. Filtering happens at plot time, not at parse time.
    n = note("a", mood="euphoric")
    assert Note.from_markdown(n.to_markdown(), fallback_id="a").mood == "euphoric"


def test_absent_pain_parses_as_none():
    md = "---\nid: a\ntitle: a\n---\n"
    assert Note.from_markdown(md, fallback_id="a").pain is None


def test_out_of_range_pain_on_disk_is_clamped_on_read():
    md = "---\nid: a\ntitle: a\npain: 42\n---\n"
    assert Note.from_markdown(md, fallback_id="a").pain == 10


def test_index_keeps_pain(tmp_path):
    db = Database(tmp_path / "i.sqlite")
    db.upsert(note("a", mood="low", pain=6))
    assert db.get("a").pain == 6


def test_clearing_a_mood_clears_it_in_the_index_too(tmp_path):
    """Regression: the index used to keep a mood the file no longer had.

    `upsert` passed the mood through COALESCE, so a note saved with no mood
    kept whatever the previous save had stored. The file was correct and the
    index was stale, which made search and the index disagree with the vault.
    """
    db = Database(tmp_path / "i.sqlite")
    db.upsert(note("a", mood="good", pain=3))
    assert db.get("a").mood == "good"

    db.upsert(note("a", mood=None, pain=None))
    assert db.get("a").mood is None
    assert db.get("a").pain is None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def client(tmp_path):
    return TestClient(create_app(vault_root=tmp_path))


def test_mood_endpoint_on_an_empty_vault_returns_a_usable_shape(tmp_path):
    got = client(tmp_path).get("/api/mood").json()
    assert got["days"] == []
    assert got["summary"]["days_logged"] == 0
    # The level vocabulary is served, not hard-coded in the CSS.
    assert got["levels"] == ["great", "good", "meh", "low", "bad"]
    assert got["pain_range"] == {"min": 0, "max": 10}
    assert got["from"] <= got["to"]


def test_a_note_with_a_mood_shows_up_in_the_series(tmp_path):
    c = client(tmp_path)
    today = date.today().isoformat()
    c.post("/api/notes", json={
        "id": "m1", "collection": "inbox", "title": "rough day",
        "mood": "low", "pain": 7, "dates": [today],
    })
    got = c.get("/api/mood").json()
    assert [d["date"] for d in got["days"]] == [today]
    assert got["days"][0]["mood"] == "low"
    assert got["days"][0]["pain"] == 7
    assert got["summary"]["days_logged"] == 1
    assert got["summary"]["logged_today"] is True
    assert got["summary"]["streak"] == 1


def test_pain_is_clamped_through_the_api(tmp_path):
    c = client(tmp_path)
    c.post("/api/notes", json={
        "id": "m1", "collection": "inbox", "title": "t", "pain": 99,
    })
    assert c.get("/api/notes/m1").json()["pain"] == 10


def test_patch_can_set_and_clear_mood_and_pain(tmp_path):
    c = client(tmp_path)
    c.post("/api/notes", json={"id": "m1", "collection": "inbox", "title": "t"})

    c.patch("/api/notes/m1", json={"mood": "good", "pain": 4})
    body = c.get("/api/notes/m1").json()
    assert (body["mood"], body["pain"]) == ("good", 4)

    # Absent keys keep their value.
    c.patch("/api/notes/m1", json={"title": "renamed"})
    body = c.get("/api/notes/m1").json()
    assert (body["mood"], body["pain"]) == ("good", 4)

    # Explicit nulls clear them — and the file must agree with the index.
    c.patch("/api/notes/m1", json={"mood": None, "pain": None})
    body = c.get("/api/notes/m1").json()
    assert body["mood"] is None
    assert body["pain"] is None
    on_disk = (tmp_path / "inbox" / "m1.md").read_text()
    assert "mood: good" not in on_disk


def test_a_bad_range_is_rejected_rather_than_silently_reordered(tmp_path):
    r = client(tmp_path).get("/api/mood?start=2026-09-30&end=2026-09-01")
    assert r.status_code == 400


def test_days_parameter_narrows_the_window(tmp_path):
    c = client(tmp_path)
    got_broad = c.get("/api/mood?days=3650").json()
    got_narrow = c.get("/api/mood?days=7").json()
    assert got_narrow["from"] > got_broad["from"]
    assert got_narrow["to"] == got_broad["to"]
