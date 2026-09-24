"""TDD: notes to ICS feed."""
from datetime import date
from app.models import Note, Signifier, Status
from app.ics import notes_to_ics


def test_ics_single_date_note():
    n = Note(id="x", collection="inbox", title="Buy milk",
             body="oat", signifier=Signifier.TASK, status=Status.OPEN,
             dates=[date(2026, 9, 23)])
    ics = notes_to_ics([n])
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "END:VEVENT" in ics
    assert "SUMMARY:Buy milk" in ics
    assert "DTSTART;VALUE=DATE:20260923" in ics
    assert "DTEND;VALUE=DATE:20260924" in ics  # all-day events end day+1
    assert "UID:x-2026-09-23@nookboard" in ics


def test_ics_multi_date_one_vevent_per_date():
    n = Note(id="y", collection="home", title="Two",
             body="body", signifier=Signifier.EVENT, status=Status.OPEN,
             dates=[date(2026, 9, 1), date(2026, 9, 2)])
    ics = notes_to_ics([n])
    assert ics.count("BEGIN:VEVENT") == 2
    assert "DTSTART;VALUE=DATE:20260901" in ics
    assert "DTSTART;VALUE=DATE:20260902" in ics


def test_ics_no_dates_no_events():
    n = Note(id="z", collection="home", title="untimed", body="",
             signifier=Signifier.NOTE, status=Status.OPEN, dates=[])
    ics = notes_to_ics([n])
    assert "BEGIN:VEVENT" not in ics
    assert "BEGIN:VCALENDAR" in ics


def test_ics_escapes_commas_and_semicolons():
    n = Note(id="e", collection="inbox", title="a, b; c",
             body="line1\nline2", signifier=Signifier.TASK, status=Status.OPEN,
             dates=[date(2026, 9, 23)])
    ics = notes_to_ics([n])
    assert "SUMMARY:a\\, b\\; c" in ics
    assert "DESCRIPTION:line1\\nline2" in ics


def _timed(**kw):
    fields = dict(id="t", collection="inbox", title="Standup", body="",
                  signifier=Signifier.EVENT, status=Status.OPEN,
                  dates=[date(2026, 9, 24)], at="14:30")
    fields.update(kw)
    return Note(**fields)


def test_a_timed_note_is_a_timed_event_not_an_all_day_one():
    ics = notes_to_ics([_timed()])
    assert "DTSTART:20260924T143000" in ics
    # floating: no trailing Z, no TZID parameter
    assert "DTSTART:20260924T143000Z" not in ics
    assert "TZID" not in ics
    # and it is no longer a whole-day event
    assert "VALUE=DATE" not in ics


def test_a_timed_note_without_an_end_gets_the_default_hour():
    ics = notes_to_ics([_timed()])
    assert "DTEND:20260924T153000" in ics


def test_an_end_is_published_as_written():
    ics = notes_to_ics([_timed(until="16:00")])
    assert "DTSTART:20260924T143000" in ics
    assert "DTEND:20260924T160000" in ics


def test_an_overnight_event_ends_the_next_day():
    """23:00-01:00 is two hours long. Emitting DTEND before DTSTART would make
    a calendar client compute a negative duration, or drop the event."""
    ics = notes_to_ics([_timed(at="23:00", until="01:00")])
    assert "DTSTART:20260924T230000" in ics
    assert "DTEND:20260925T010000" in ics


def test_a_timed_event_is_busy_and_an_all_day_note_is_not():
    timed = notes_to_ics([_timed()])
    all_day = notes_to_ics([Note(id="d", collection="inbox", title="Deadline", body="",
                                 signifier=Signifier.EVENT, status=Status.OPEN,
                                 dates=[date(2026, 9, 24)])])
    assert "TRANSP:OPAQUE" in timed
    assert "TRANSP:TRANSPARENT" in all_day


def test_the_same_note_on_several_days_is_several_timed_events():
    many = _timed(dates=[date(2026, 9, 24), date(2026, 9, 25)])
    ics = notes_to_ics([many])
    assert ics.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20260924T143000" in ics
    assert "DTSTART:20260925T143000" in ics
    # the UID is per note *and* day, so a client updates rather than duplicates
    assert "UID:t-2026-09-24@nookboard" in ics
    assert "UID:t-2026-09-25@nookboard" in ics


def test_a_time_with_no_date_emits_no_event():
    floating = _timed(dates=[])
    assert "BEGIN:VEVENT" not in notes_to_ics([floating])


def test_ics_crlf_line_endings():
    n = Note(id="x", collection="inbox", title="t", body="",
             signifier=Signifier.TASK, status=Status.OPEN,
             dates=[date(2026, 9, 23)])
    ics = notes_to_ics([n])
    # RFC 5545 mandates CRLF
    assert "\r\n" in ics
    # and there should be at least one CRLF between VEVENT components
    assert ics.count("\r\n") > 5