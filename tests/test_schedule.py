"""A note's time: what a time means, and when something should happen.

The interesting cases are all at the edges. A time that is not a time must not
schedule anything, a time with no day has no instant to name, an end before a
start crosses midnight rather than collapsing, and YAML reads an unquoted
`14:30` as the number 870 — which is a real trap, because `0930` becomes 930 and
the two are indistinguishable once parsed.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pytest

from app import schedule
from app.models import Note


def note(**kw) -> Note:
    fields = dict(id="n1", collection="inbox", title="Standup", body="")
    fields.update(kw)
    return Note(**fields)


def md(at: str | None = None, until: str | None = None, dates="[2026-09-24]") -> str:
    lines = ["---", "id: x", "title: T", f"dates: {dates}"]
    if at is not None:
        lines.append(f"at: {at}")
    if until is not None:
        lines.append(f"until: {until}")
    lines.append("---")
    return "\n".join(lines) + "\nbody\n"


# -- parse_time -------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("14:30", "14:30"),
        ("09:05", "09:05"),
        ("9:05", "09:05"),
        ("14:30:00", "14:30"),      # seconds are not a resolution we act on
        ("1430", "14:30"),
        ("930", "09:30"),
        ("9", "09:00"),
        ("00:00", "00:00"),
        ("23:59", "23:59"),
    ],
)
def test_the_shapes_a_person_writes(raw, expected):
    assert schedule.parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["25:00", "12:60", "noon", "half past", "9.30", "", "  ", "-1:00"])
def test_something_that_is_not_a_time_is_not_one(raw):
    """Dropped, never rounded. An unreadable time makes the note untimed, which
    is a smaller lie than scheduling it at the wrong hour."""
    assert schedule.parse_time(raw) is None


def test_the_yaml_integer_is_decoded_the_way_yaml_encoded_it():
    """`at: 14:30` unquoted is base-60 to a YAML loader: the integer 870.

    870 and 930 are two different times; reading either as "minutes past
    midnight" would be wrong by hours, so they are decoded base-60.
    """
    assert schedule.parse_time(870) == "14:30"
    assert schedule.parse_time(545) == "09:05"
    # 930 is `0930` (a time) and also the base-60 encoding of 15:30. The raw
    # text decides that one -- parse_time alone gets the base-60 reading.
    assert schedule.parse_time(930) == "15:30"


def test_a_bare_integer_is_an_hour():
    """`at: 9` means nine o'clock. A colon form that small (`0:05`) stays a
    string through YAML, so an integer this small cannot be anything else."""
    assert schedule.parse_time(9) == "09:00"
    assert schedule.parse_time(0) == "00:00"


def test_booleans_and_datetimes_from_yaml():
    assert schedule.parse_time(True) is None
    assert schedule.parse_time(datetime(2026, 9, 24, 14, 30)) == "14:30"


# -- reading the text as written -------------------------------------------

def test_unquoted_frontmatter_wins_over_what_yaml_made_of_it():
    """The whole reason the raw text is read: `0930` and `14:30` are both eaten
    by YAML, and 930 is a *different time* depending on which way you decode
    it. The text is the truth; the parsed value is the fallback."""
    assert Note.from_markdown(md(at="0930"), fallback_id="x").at == "09:30"
    assert Note.from_markdown(md(at="14:30"), fallback_id="x").at == "14:30"
    assert Note.from_markdown(md(at="9:05"), fallback_id="x").at == "09:05"


def test_quoted_frontmatter_and_a_comment_line():
    assert Note.from_markdown(md(at="'14:30'"), fallback_id="x").at == "14:30"
    assert Note.from_markdown(md(at='"14:30"'), fallback_id="x").at == "14:30"
    body = md(at="14:30").replace("at: 14:30", "# at: 09:00\nat: 14:30")
    assert Note.from_markdown(body, fallback_id="x").at == "14:30"


def test_an_empty_or_absent_time_is_simply_untimed():
    assert Note.from_markdown(md(), fallback_id="x").at is None
    assert Note.from_markdown(md(at=""), fallback_id="x").at is None
    raw = schedule.raw_time_from_frontmatter("no frontmatter at all\n", "at")
    assert raw is None


def test_a_crlf_file_still_reads():
    """An Obsidian vault on another machine writes Windows line endings."""
    body = md(at="0930").replace("\n", "\r\n")
    assert Note.from_markdown(body, fallback_id="x").at == "09:30"


# -- the round trip --------------------------------------------------------

def test_a_written_note_reads_back_identical():
    """Whatever we write, we must read. Before this the app wrote the raw field
    and read a normalized one, so `at: 9:05` came back as `09:05` and the note
    was not equal to itself."""
    for raw in ("9:05", "09:05", "14:30", "1430", "9", "23:59"):
        parsed = Note.from_markdown(md(at=raw), fallback_id="x")
        again = Note.from_markdown(parsed.to_markdown(), fallback_id="x")
        assert again == parsed, raw
        assert again.at == parsed.at


def test_what_the_app_writes_is_never_the_ambiguous_form():
    """Zero-padded, and quoted by the dumper when plain would be base-60. A file
    the app wrote must not depend on our raw-text scan to be read correctly."""
    written = note(dates=[date(2026, 9, 24)], at="9:05", until="09:45").to_markdown()
    assert "at: 09:05" in written and "until: 09:45" in written
    # Anchored: `09:05` contains `9:05`, so a bare substring check passes on the
    # wrong file and fails on the right one.
    assert re.search(r"^\s*at:\s*9:05\s*$", written, re.M) is None


def test_an_untimed_note_is_untouched():
    plain = note(dates=[date(2026, 9, 24)])
    assert "at: null" in plain.to_markdown()
    assert schedule.is_timed(plain) is False
    assert schedule.label(plain) == ""


# -- the instant -----------------------------------------------------------

def test_a_time_with_no_date_is_not_scheduled():
    """There is no instant to name, so nothing fires and no event is emitted.
    The time is still stored — what you wrote is never thrown away."""
    floating = note(at="14:30")
    assert schedule.is_timed(floating) is True
    assert schedule.starts_at(floating, date(2026, 9, 24)) == datetime(2026, 9, 24, 14, 30)
    assert list(schedule.occurrences([floating])) == []


def test_the_default_duration_is_an_hour_and_until_beats_it():
    day = date(2026, 9, 24)
    assert schedule.ends_at(note(at="14:30"), day) == datetime(2026, 9, 24, 15, 30)
    assert schedule.ends_at(note(at="14:30", until="16:00"), day) == datetime(2026, 9, 24, 16, 0)


def test_an_end_before_its_start_crosses_midnight():
    """23:00–01:00 is two hours, not minus twenty-two."""
    day = date(2026, 9, 24)
    end = schedule.ends_at(note(at="23:00", until="01:00"), day)
    assert end == datetime(2026, 9, 25, 1, 0)
    assert end - schedule.starts_at(note(at="23:00"), day) == schedule.timedelta(hours=2)


def test_an_identical_start_and_end_is_a_full_day_not_zero():
    day = date(2026, 9, 24)
    assert schedule.ends_at(note(at="14:30", until="14:30"), day) == datetime(2026, 9, 25, 14, 30)


def test_the_time_applies_to_every_day_the_note_carries():
    """A workshop listed on three days at 14:00 is at 14:00 on all three."""
    many = note(dates=[date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26)], at="14:00")
    got = [occ.start.day for occ in schedule.occurrences([many])]
    assert got == [24, 25, 26]


def test_the_label_shows_an_end_only_when_one_was_written():
    """An invented 15:30 beside your event would read as something you wrote."""
    assert schedule.label(note(at="14:30")) == "14:30"
    assert schedule.label(note(at="14:30", until="16:00")) == "14:30\u201316:00"
    assert schedule.label(note(at="9:05", until="09:45")) == "09:05\u201309:45"


# -- what should fire ------------------------------------------------------

def test_due_fires_once_at_the_time_and_not_before():
    event = note(dates=[date(2026, 9, 24)], at="14:30")
    assert schedule.due([event], datetime(2026, 9, 24, 14, 29)) == []
    found = schedule.due([event], datetime(2026, 9, 24, 14, 30))
    assert [o.key for o in found] == ["n1@2026-09-24T14:30"]


def test_due_skips_what_was_already_announced():
    event = note(dates=[date(2026, 9, 24)], at="14:30")
    now = datetime(2026, 9, 24, 14, 31)
    assert len(schedule.due([event], now)) == 1
    assert schedule.due([event], now, fired=["n1@2026-09-24T14:30"]) == []


def test_an_untimed_note_is_never_due():
    """A note without a time belongs to a whole day, and a whole day is not
    something to interrupt someone about."""
    todo = note(dates=[date(2026, 9, 24)])
    assert schedule.due([todo], datetime(2026, 9, 24, 23, 59)) == []


def test_since_stops_a_week_of_sleep_from_becoming_a_week_of_popups():
    """A machine that was off since Monday should catch up on *today*, not
    deliver every appointment it slept through. That is not a reminder, it is an
    accusation."""
    monday = note(id="mon", collection="inbox", title="Mon", body="", dates=[date(2026, 9, 21)], at="09:00")
    today = note(id="tue", collection="inbox", title="Tue", body="", dates=[date(2026, 9, 24)], at="09:00")
    now = datetime(2026, 9, 24, 12, 0)
    floor = datetime(2026, 9, 24, 0, 0)
    assert [o.note.id for o in schedule.due([monday, today], now)] == ["mon", "tue"]
    assert [o.note.id for o in schedule.due([monday, today], now, since=floor)] == ["tue"]


def test_missed_things_are_read_in_the_order_they_happened():
    later = note(id="b", collection="inbox", title="Later", body="", dates=[date(2026, 9, 24)], at="16:00")
    earlier = note(id="a", collection="inbox", title="Earlier", body="", dates=[date(2026, 9, 24)], at="09:00")
    got = schedule.due([later, earlier], datetime(2026, 9, 24, 17, 0), since=datetime(2026, 9, 24))
    assert [o.note.id for o in got] == ["a", "b"]


def test_how_late_says_how_late():
    start = datetime(2026, 9, 24, 14, 30)
    assert schedule.how_late(start, datetime(2026, 9, 24, 14, 31)) is None  # needs no comment
    assert schedule.how_late(start, datetime(2026, 9, 24, 14, 45)) == "15 minutes ago"
    assert schedule.how_late(start, datetime(2026, 9, 24, 16, 30)) == "2 hours ago"
    assert schedule.how_late(start, datetime(2026, 9, 24, 16, 45)) == "2h 15m ago"
    assert schedule.how_late(start, datetime(2026, 9, 26, 14, 30)) == "2 days ago"


def test_a_note_without_a_time_is_reported_as_having_none():
    """`starts_at` must not invent midnight: an all-day note and a note at
    00:00 are different things and the ICS feed treats them differently."""
    assert schedule.starts_at(note(), date(2026, 9, 24)) is None
    assert schedule.ends_at(note(), date(2026, 9, 24)) is None
