"""Weekly notes: the schedule, the rollup, and the fence.

Weeks are constructed with `date.fromisocalendar` rather than written out as
literals, so the tests cannot quietly encode an off-by-one about which Monday a
week starts on.
"""

from datetime import date, datetime, time, timedelta

import pytest

from app import daily, weekly
from app.db import Database
from app.models import Note, Signifier, Status

#: Monday and Sunday of ISO week 38 of 2026, and the Monday the review is written.
W38_MONDAY = date.fromisocalendar(2026, 38, 1)
W38_WEDNESDAY = date.fromisocalendar(2026, 38, 3)
W38_SUNDAY = date.fromisocalendar(2026, 38, 7)
REVIEW_DAY = W38_MONDAY + timedelta(days=7)  # the Monday after the week ends
TODAY = date.today()
#: Always the previous ISO week: weeks are seven days, so subtracting seven
#: always lands in the week before, whatever day it is today.
LAST_WEEK_DAY = TODAY - timedelta(days=7)
LAST_WEEK = weekly.week_key(LAST_WEEK_DAY)


def _done(nid: str, day: date) -> Note:
    return Note(
        id=nid,
        collection="inbox",
        title=nid,
        body="",
        signifier=Signifier.TASK,
        status=Status.COMPLETE,
        dates=[day],
        completed=day,
    )


# --- the week itself ------------------------------------------------------

def test_a_week_key_round_trips_through_its_bounds():
    """The property that matters: a day is always inside its own week."""
    for offset in range(0, 400, 7):
        day = date(2026, 1, 1) + timedelta(days=offset)
        monday, sunday = weekly.week_bounds(weekly.week_key(day))
        assert monday <= day <= sunday, day
        assert monday.weekday() == 0 and sunday.weekday() == 6


def test_a_week_key_is_the_iso_week_not_a_calendar_week():
    assert weekly.week_key(date(2026, 9, 21)) == "2026-W39"
    # 1 January can belong to the previous ISO year -- the reason this is not
    # computed from the month and day.
    assert weekly.week_key(date(2027, 1, 1)).startswith("2026-W")


def test_the_note_id_follows_the_key():
    assert weekly.note_id("2026-W39") == "weekly-2026-W39"
    # The generated-container guards are regexes, and an id that does not match
    # them is guarded by nothing. Checked here because the ids and the regexes
    # live in different modules and drifting apart is silent.
    from app import models

    assert models.is_weekly_note_id(weekly.note_id("2026-W39"))
    assert models.is_generated_note_id(weekly.note_id("2026-W39"))


# --- when a review is owed ------------------------------------------------

def test_a_week_is_not_reviewed_while_it_is_still_running():
    """Sunday evening is not the end of a week that includes Sunday."""
    notes = [_done("a", W38_WEDNESDAY)]
    for when in (time(21, 0), time(22, 5), time(23, 59)):
        got = weekly.due_weeks(
            notes,
            today=W38_SUNDAY,
            hour=22,
            now=datetime.combine(W38_SUNDAY, when),
            blocked=set(),
        )
        assert got == [], when


def test_the_review_is_written_on_the_monday_after_the_cutoff():
    notes = [_done("a", W38_WEDNESDAY)]
    key = weekly.week_key(W38_MONDAY)

    early = weekly.due_weeks(
        notes,
        today=REVIEW_DAY,
        hour=22,
        now=datetime.combine(REVIEW_DAY, time(10, 0)),
        blocked=set(),
    )
    assert early == [], "not before the cutoff hour"

    late = weekly.due_weeks(
        notes,
        today=REVIEW_DAY,
        hour=22,
        now=datetime.combine(REVIEW_DAY, time(22, 5)),
        blocked=set(),
    )
    assert late == [key]


def test_a_week_with_nothing_finished_gets_no_note():
    got = weekly.due_weeks(
        [],
        today=REVIEW_DAY,
        hour=22,
        now=datetime.combine(REVIEW_DAY, time(22, 5)),
        blocked=set(),
    )
    assert got == []


def test_only_the_newest_owed_week_is_written_per_tick():
    """A backlog drains over ticks rather than in one burst of model calls."""
    notes = [
        _done("a", W38_WEDNESDAY),
        _done("b", W38_WEDNESDAY - timedelta(days=7)),
        _done("c", W38_WEDNESDAY - timedelta(days=14)),
    ]
    got = weekly.due_weeks(
        notes,
        today=REVIEW_DAY,
        hour=22,
        now=datetime.combine(REVIEW_DAY, time(22, 5)),
        blocked=set(),
    )
    assert got == [weekly.week_key(W38_MONDAY)]


def test_a_capped_window_stops_reaching_further_back():
    old = W38_WEDNESDAY - timedelta(days=7 * (weekly.CATCHUP_WEEKS + 2))
    got = weekly.due_weeks(
        [_done("a", old)],
        today=REVIEW_DAY,
        hour=22,
        now=datetime.combine(REVIEW_DAY, time(22, 5)),
        blocked=set(),
    )
    assert got == []


def test_a_blocked_week_is_not_owed_again():
    notes = [_done("a", W38_WEDNESDAY)]
    got = weekly.due_weeks(
        notes,
        today=REVIEW_DAY,
        hour=22,
        now=datetime.combine(REVIEW_DAY, time(22, 5)),
        blocked={weekly.week_key(W38_MONDAY)},
    )
    assert got == []


# --- what the week holds --------------------------------------------------

def test_finished_work_is_grouped_by_day_and_empty_days_are_left_out():
    from app.models import Signifier as S

    notes = [
        _done("mon", W38_MONDAY),
        _done("wed", W38_WEDNESDAY),
        _done("mon2", W38_MONDAY),
    ]
    got = weekly.finished_in(notes, weekly.week_key(W38_MONDAY))
    assert sorted(got) == [W38_MONDAY, W38_WEDNESDAY]
    assert [n.title for n in got[W38_MONDAY]] == ["mon", "mon2"]
    assert [n.title for n in got[W38_WEDNESDAY]] == ["wed"]


def test_an_abandoned_note_is_not_finished_work():
    struck = Note(
        id="struck",
        collection="inbox",
        title="struck",
        body="",
        signifier=Signifier.NOTE,
        status=Status.IRRELEVANT,
        dates=[W38_MONDAY],
        completed=W38_MONDAY,
    )
    assert weekly.finished_in([struck], weekly.week_key(W38_MONDAY)) == {}


# --- the generated section ------------------------------------------------

def test_the_section_carries_the_list_and_the_prose():
    done = {W38_MONDAY: [_done("ship-it", W38_MONDAY)]}
    section = weekly.render("2026-W38", done, "A productive week.", "Logged 5 of 7 days.")
    assert weekly.MARK_START in section and weekly.MARK_END in section
    assert "## Week 2026-W38" in section
    assert "A productive week." in section
    assert "[[ship-it]]" in section
    assert "Logged 5 of 7 days." in section


def test_the_week_feels_line_is_absent_when_nothing_was_logged():
    assert weekly.felt_line(None) is None
    assert weekly.felt_line({"days_logged": 0}) is None
    line = weekly.felt_line({"days_logged": 5, "avg_mood": "meh", "avg_pain": 4.2})
    assert line == "Logged 5 of 7 days · average mood meh · average pain 4.2/10."


def test_a_week_with_no_insight_reading_omits_the_line():
    section = weekly.render("2026-W38", {}, None, None, None)
    assert "### Finished" not in section
    assert "_Nothing was marked done._" in section
    assert "Pain" not in section


def test_regenerating_preserves_what_was_typed_around_it():
    first = weekly.upsert_section("", weekly.render("2026-W38", {}, "v1.", None))
    edited = "My own note at the top.\n\n" + first + "\nAnd something after.\n"
    again = weekly.upsert_section(edited, weekly.render("2026-W38", {}, "v2.", None))
    assert again.count(weekly.MARK_START) == 1
    assert "My own note at the top." in again
    assert "And something after." in again
    assert "v2." in again
    assert "v1." not in again


def test_the_note_is_dated_the_day_the_week_ended():
    note = weekly.note_for("2026-W38", {}, "v1.", None)
    assert note.id == weekly.note_id("2026-W38")
    assert note.title == "2026-W38"
    assert note.collection == "weekly"
    assert note.dates == [W38_SUNDAY], "one date, so it is not printed seven times"
    assert weekly.has_summary(note.body)


# --- a generated note is not work -----------------------------------------

def test_a_weekly_note_does_not_count_as_the_week_s_own_work():
    weeknote = Note(
        id=weekly.note_id("2026-W38"),
        collection="weekly",
        title="2026-W38",
        body="",
        signifier=Signifier.NOTE,
        status=Status.COMPLETE,
        dates=[W38_SUNDAY],
        completed=W38_SUNDAY,
    )
    notes = [weeknote, _done("real", W38_SUNDAY)]
    assert [n.title for n in daily.completed_on(notes, W38_SUNDAY)] == ["real"]


# --- through the API ------------------------------------------------------

def _chunk(content):
    return {"choices": [{"delta": {"content": content}, "finish_reason": None}]}


def test_the_api_writes_a_week_note_into_the_previous_week(client_factory, seed_task):
    seed_task("ship-it", "Ship the zine", completed=LAST_WEEK_DAY)
    c = client_factory()
    c.set_script([_chunk("A steady week.")])

    got = c.post("/api/weekly/summary", json={}).json()
    assert got["week"] == LAST_WEEK
    assert got["wrote"] is True
    assert got["recap"] == "A steady week."
    assert got["completed"] == 1

    note = c.get(f"/api/notes/{weekly.note_id(LAST_WEEK)}").json()
    assert weekly.MARK_START in note["body"]
    # Linked by title, so the rollup is clickable and the task grows a backlink
    # to the week it belongs to.
    assert "[[Ship the zine]]" in note["body"]
    assert "A steady week." in note["body"]


def test_the_ledger_stops_the_week_being_written_twice(client_factory, seed_task):
    seed_task("ship-it", "Ship the zine", completed=LAST_WEEK_DAY)
    c = client_factory()
    c.set_script([_chunk("A steady week.")])
    c.post("/api/weekly/summary", json={})

    state = c.get("/api/weekly").json()
    assert LAST_WEEK in state["summarised"]
    assert state["owed"] == [], "a settled week is not owed again"
    assert state["retrying"] == {}


def test_a_week_with_nothing_in_it_is_not_written(client_factory):
    c = client_factory()
    c.set_script([_chunk("Should not be asked for.")])
    got = c.post("/api/weekly/summary", json={}).json()
    assert got["wrote"] is False
    assert got["reason"] == "nothing was completed that week"


def test_the_week_state_endpoint_reports_the_days_it_covers(client_factory, seed_task):
    seed_task("ship-it", "Ship the zine", completed=LAST_WEEK_DAY)
    c = client_factory()
    c.set_script([_chunk("A steady week.")])
    c.post("/api/weekly/summary", json={})

    got = c.get(f"/api/weekly/{LAST_WEEK}").json()
    assert got["week"] == LAST_WEEK
    assert got["completed"] == 1
    assert got["days"] == {LAST_WEEK_DAY.isoformat(): ["Ship the zine"]}
    assert got["has_summary"] is True
    assert got["generated"] is True


def test_a_malformed_week_is_refused_rather_than_guessed_at(client_factory):
    c = client_factory()
    assert c.get("/api/weekly/2026-39").status_code == 400
    assert c.get("/api/weekly/2026-W99").status_code == 400
    assert c.post("/api/weekly/summary", json={"week": "last tuesday"}).status_code == 400


def test_a_week_key_is_normalised(client_factory, seed_task):
    """`2026-W3` and `2026-W03` are the same week, so both must address it."""
    seed_task("ship-it", "Ship the zine", completed=LAST_WEEK_DAY)
    c = client_factory()
    loose = LAST_WEEK.replace("-W0", "-W")
    assert c.get(f"/api/weekly/{loose}").json()["week"] == LAST_WEEK


def test_re_running_a_week_replaces_its_section_rather_than_adding_one(
    client_factory, seed_task
):
    """An explicit POST always runs; the ledger governs the *scheduler*.

    Which is why `refresh` lives on the endpoint at all: it un-settles the week,
    so a run the scheduler had given up on can be picked up again.
    """
    seed_task("ship-it", "Ship the zine", completed=LAST_WEEK_DAY)
    c = client_factory()
    c.set_script([_chunk("First pass.")])
    c.post("/api/weekly/summary", json={})

    c.set_script([_chunk("Second pass.")])
    again = c.post("/api/weekly/summary", json={"refresh": True}).json()
    assert again["recap"] == "Second pass."

    body = c.get(f"/api/notes/{weekly.note_id(LAST_WEEK)}").json()["body"]
    assert body.count(weekly.MARK_START) == 1, "one section, not two"
    assert "Second pass." in body and "First pass." not in body


def test_an_edit_made_while_the_model_thinks_survives_for_weeks_too(
    client_factory, seed_task, tmp_path, monkeypatch
):
    """The same stale-read trap as the daily run, at a larger scale."""
    seed_task("ship-it", "Ship the zine", completed=LAST_WEEK_DAY)
    c = client_factory()
    c.set_script([_chunk("First pass.")])
    c.post("/api/weekly/summary", json={})

    from app.llm import LlamaCpp

    note_id = weekly.note_id(LAST_WEEK)

    async def complete_while_the_user_types(self, messages, **kwargs):
        path = next(tmp_path.rglob(f"{note_id}.md"))
        text = path.read_text()
        path.write_text(text.replace(weekly.MARK_START, f"BUY INK\n\n{weekly.MARK_START}", 1))
        return "A recap."

    monkeypatch.setattr(LlamaCpp, "complete", complete_while_the_user_types)
    c.post("/api/weekly/summary", json={"week": LAST_WEEK, "refresh": True})

    body = c.get(f"/api/notes/{note_id}").json()["body"]
    assert "BUY INK" in body, body


def test_a_weekly_note_is_kept_off_the_board_too(client_factory, seed_task):
    """The same trap the daily note sprang: the container is not a piece of work."""
    seed_task("ship-it", "Ship the zine", completed=LAST_WEEK_DAY)
    seed_task(
        weekly.note_id(LAST_WEEK), LAST_WEEK,
        completed=LAST_WEEK_DAY, status="open", collection="weekly",
    )
    c = client_factory()
    board = c.get("/api/board").json()
    ids = [card["id"] for col in board["columns"] for card in col["cards"]]

    assert weekly.note_id(LAST_WEEK) not in ids, "a rollup is not a card"
    assert "ship-it" in ids
    assert board["hidden_generated"] == 1


def test_a_weekly_note_is_not_a_recurrence_parent(tmp_path):
    """The same trap as a daily note: a `<week>-<next>` note collecting forever."""
    db = Database(tmp_path / ".index.sqlite")
    db.upsert(
        Note(
            id=weekly.note_id("2026-W38"),
            collection="weekly",
            title="2026-W38",
            body="",
            signifier=Signifier.NOTE,
            status=Status.OPEN,
            dates=[W38_SUNDAY],
            recurrence="weekly",
        )
    )
    assert db.run_recurring(REVIEW_DAY + timedelta(days=60)) == []
