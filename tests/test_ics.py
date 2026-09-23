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


def test_ics_crlf_line_endings():
    n = Note(id="x", collection="inbox", title="t", body="",
             signifier=Signifier.TASK, status=Status.OPEN,
             dates=[date(2026, 9, 23)])
    ics = notes_to_ics([n])
    # RFC 5545 mandates CRLF
    assert "\r\n" in ics
    # and there should be at least one CRLF between VEVENT components
    assert ics.count("\r\n") > 5