"""TDD: daily notes — a day's finished work, summarised into that day's note.

The parts worth testing hard are the ones that go wrong quietly: a completion
date that drifts every time the note is edited, a regenerated section that eats
what was written by hand around it, and a model that is down costing you the
record of what you actually did.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import daily
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import Note, Signifier, Status

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def _note(
    nid: str,
    title: str,
    *,
    completed: date | None = None,
    status: Status = Status.COMPLETE,
    collection: str = "inbox",
) -> Note:
    return Note(
        id=nid,
        collection=collection,
        title=title,
        body="",
        signifier=Signifier.TASK,
        status=status,
        dates=[],
        completed=completed,
    )


def _seed(root: Path, note: Note) -> None:
    """Write a note straight to disk, so the boot rebuild indexes it.

    A completion date is stamped as *today* when a task is completed, so a
    summary of a past day can only be reached by seeding one.
    """
    folder = root / note.collection
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{note.id}.md").write_text(
        "\n".join(
            [
                "---",
                f"id: {note.id}",
                f"collection: {note.collection}",
                f"title: {note.title}",
                f"signifier: {note.signifier.value}",
                f"status: {note.status.value}",
                "dates: []",
                f"created: {note.created.isoformat()}",
                f"completed: {note.completed.isoformat() if note.completed else 'null'}",
                "tags: []",
                "---",
                "",
                note.body,
            ]
        )
    )


def _chunk(*, content=None, finish=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


@pytest.fixture
def make_client(tmp_path: Path, sse_server):
    """Build an app over a vault seeded *before* boot, wired to the fake model."""
    base, set_script = sse_server

    def _make(notes=()):
        for note in notes:
            _seed(tmp_path, note)
        settings = Settings(
            vault=tmp_path,
            llm_url=base,
            llm_model="fake",
            llm_max_tokens=2048,
            llm_timeout=30.0,
        )
        c = TestClient(create_app(settings=settings))
        c.vault_root = tmp_path  # type: ignore[attr-defined]
        c.set_script = set_script  # type: ignore[attr-defined]
        return c

    return _make


# --- what belongs to a day ------------------------------------------------

def test_completed_on_takes_only_that_day():
    notes = [
        _note("a", "Today", completed=TODAY),
        _note("b", "Yesterday", completed=YESTERDAY),
        _note("c", "Undated"),
    ]
    assert [n.title for n in daily.completed_on(notes, TODAY)] == ["Today"]


def test_completed_on_ignores_abandoned_and_unfinished_work():
    """A struck note sits in the Done column but was not done."""
    notes = [
        _note("a", "Finished", completed=TODAY),
        _note("b", "Abandoned", completed=TODAY, status=Status.IRRELEVANT),
        _note("c", "Still open", completed=None),
        _note("d", "Migrated", completed=TODAY, status=Status.MIGRATED),
    ]
    assert [n.title for n in daily.completed_on(notes, TODAY)] == ["Finished"]


def test_completed_on_is_ordered_stably():
    notes = [
        _note("a", "zebra", completed=TODAY),
        _note("b", "Apple", completed=TODAY),
    ]
    assert [n.title for n in daily.completed_on(notes, TODAY)] == ["Apple", "zebra"]


# --- the generated section ------------------------------------------------

def test_render_lists_what_was_done():
    section = daily.render(TODAY, [_note("a", "Ship the zine")], "A short day.")
    assert "Ship the zine" in section
    assert "A short day." in section
    assert daily.MARK_START in section and daily.MARK_END in section


def test_render_names_the_collection_only_when_it_says_something():
    section = daily.render(
        TODAY,
        [_note("a", "Fix printer"), _note("b", "Print run", collection="zines")],
        None,
    )
    assert "- Fix printer\n" in section
    assert "- Print run — zines" in section


def test_render_survives_a_missing_recap():
    """The list is the part that is always true; the prose is optional."""
    section = daily.render(TODAY, [_note("a", "Ship the zine")], None)
    assert "Ship the zine" in section


def test_render_says_so_when_nothing_was_done():
    assert "Nothing was marked done" in daily.render(TODAY, [], None)


def test_upsert_appends_to_a_note_with_no_section():
    out = daily.upsert_section("Made soup.", daily.render(TODAY, [_note("a", "A")], None))
    assert out.startswith("Made soup.")
    assert daily.MARK_START in out


def test_upsert_replaces_rather_than_duplicates():
    body = daily.upsert_section("", daily.render(TODAY, [_note("a", "First")], None))
    once = daily.upsert_section(body, daily.render(TODAY, [_note("a", "Second")], None))
    assert once.count(daily.MARK_START) == 1
    assert "Second" in once
    assert "First" not in once


def test_upsert_is_idempotent():
    section = daily.render(TODAY, [_note("a", "A")], "Recap.")
    once = daily.upsert_section("", section)
    assert daily.upsert_section(once, section) == once


def test_upsert_keeps_what_the_person_wrote_before_it():
    body = "Slept badly.\n\n" + daily.render(TODAY, [_note("a", "A")], None)
    out = daily.upsert_section(body, daily.render(TODAY, [_note("a", "A")], "New."))
    assert out.startswith("Slept badly.")
    assert "New." in out


def test_upsert_keeps_text_after_the_section():
    body = (
        "Before.\n\n"
        + daily.render(TODAY, [_note("a", "A")], None)
        + "\n\nAfter: buy ink."
    )
    out = daily.upsert_section(body, daily.render(TODAY, [_note("a", "A")], "New."))
    assert out.startswith("Before.")
    assert out.rstrip().endswith("After: buy ink.")
    assert "New." in out


def test_upsert_repairs_a_half_deleted_fence():
    """A missing end marker must not cause a second, overlapping section."""
    body = "Kept.\n\n" + daily.MARK_START + "\nold generated text\n"
    out = daily.upsert_section(body, daily.render(TODAY, [_note("a", "A")], None))
    assert out.count(daily.MARK_START) == 1
    assert "old generated text" not in out
    assert out.startswith("Kept.")


def test_has_summary():
    assert daily.has_summary(daily.render(TODAY, [], None))
    assert not daily.has_summary("just a note")
    assert not daily.has_summary("")


# --- when a run is owed --------------------------------------------------

def test_is_due_only_at_or_after_the_hour():
    assert not daily.is_due(datetime(2026, 9, 23, 21, 59), 22)
    assert daily.is_due(datetime(2026, 9, 23, 22, 0), 22)
    assert daily.is_due(datetime(2026, 9, 23, 23, 30), 22)


def test_today_is_not_owed_before_the_cutoff():
    notes = [_note("a", "Done", completed=TODAY)]
    owed = daily.pending_days(
        notes, today=TODAY, hour=22, now=datetime(2026, 9, 23, 10, 0)
    )
    assert owed == []


def test_today_is_owed_after_the_cutoff():
    notes = [_note("a", "Done", completed=TODAY)]
    owed = daily.pending_days(
        notes, today=TODAY, hour=22, now=datetime(2026, 9, 23, 22, 30)
    )
    assert owed == [TODAY]


def test_yesterday_is_owed_whatever_the_time():
    """The laptop is shut at 22:00 far more often than it is open."""
    notes = [_note("a", "Done", completed=YESTERDAY)]
    owed = daily.pending_days(
        notes, today=TODAY, hour=22, now=datetime(2026, 9, 23, 9, 0)
    )
    assert owed == [YESTERDAY]


def test_a_day_with_nothing_done_is_never_owed():
    owed = daily.pending_days(
        [], today=TODAY, hour=22, now=datetime(2026, 9, 23, 23, 0)
    )
    assert owed == []


def test_a_day_already_summarised_is_not_owed_again():
    notes = [_note("a", "Done", completed=TODAY)]
    owed = daily.pending_days(
        notes,
        today=TODAY,
        hour=22,
        now=datetime(2026, 9, 23, 23, 0),
        blocked={TODAY},
    )
    assert owed == []


def test_a_weekend_away_does_not_lose_friday():
    """The realistic absence: finish work on Friday, shut the laptop, open it
    on Monday. A one-day catch-up window silently drops Friday for good."""
    friday = TODAY - timedelta(days=3)
    notes = [_note("a", "Friday work", completed=friday)]
    owed = daily.pending_days(
        notes, today=TODAY, hour=22, now=datetime(2026, 9, 23, 23, 0)
    )
    assert friday in owed


def test_a_long_absence_does_not_fire_a_burst_of_model_calls():
    """Catching up more days must not mean summarising a dozen in one tick."""
    notes = [
        _note(f"d{i}", f"Work {i}", completed=TODAY - timedelta(days=i))
        for i in range(1, 7)
    ]
    owed = daily.pending_days(
        notes, today=TODAY, hour=22, now=datetime(2026, 9, 23, 23, 0)
    )
    assert len(owed) <= daily.MAX_CATCHUP_RUNS


def test_catchup_does_not_reach_far_into_the_past():
    """A machine off for a fortnight must not wake up and do fifteen runs."""
    old = TODAY - timedelta(days=10)
    notes = [_note("a", "Ancient", completed=old)]
    owed = daily.pending_days(
        notes, today=TODAY, hour=22, now=datetime(2026, 9, 23, 23, 0)
    )
    assert old not in owed


# --- the note itself ------------------------------------------------------

def test_note_for_is_dated_on_the_day_it_covers():
    note = daily.note_for(TODAY, [_note("a", "A")], "Recap.")
    assert note.id == f"daily-{TODAY.isoformat()}"
    # The bare ISO title is what makes [[2026-09-23]] resolve.
    assert note.title == TODAY.isoformat()
    assert note.dates == [TODAY]
    assert note.collection == "daily"


def test_a_daily_note_is_not_counted_as_work():
    """A day's note is a container, not something you did.

    Marking it done is a natural thing to do -- it *is* a note you finish -- and
    without this the recap lists the page you are reading as one of the day's
    accomplishments, then keeps doing it.
    """
    dailynote = Note(
        id=daily.daily_note_id(TODAY),
        collection="daily",
        title=TODAY.isoformat(),
        body="",
        signifier=Signifier.NOTE,
        status=Status.COMPLETE,
        dates=[TODAY],
        completed=TODAY,
    )
    notes = [dailynote, _note("a", "Real work", completed=TODAY)]
    assert [n.title for n in daily.completed_on(notes, TODAY)] == ["Real work"]



# --- the completion date -------------------------------------------------

def test_completing_a_task_stamps_the_day(make_client):
    c = make_client()
    c.post("/api/notes", json={
        "id": "t1", "collection": "inbox", "title": "Ship it",
        "body": "", "signifier": "task", "status": "open",
    })
    done = c.patch("/api/notes/t1", json={"status": "complete"}).json()
    assert done["completed"] == TODAY.isoformat()


def test_the_stamp_reaches_the_file(make_client):
    c = make_client()
    c.post("/api/notes", json={
        "id": "t1", "collection": "inbox", "title": "Ship it",
        "body": "", "signifier": "task", "status": "open",
    })
    c.patch("/api/notes/t1", json={"status": "complete"})
    written = next(c.vault_root.rglob("t1.md")).read_text()
    # Assert what round-trips rather than how YAML chose to quote a date.
    assert Note.from_markdown(written).completed == TODAY


def test_editing_a_finished_task_keeps_its_date(make_client):
    """Renaming or re-saving must not move a day's work to today."""
    c = make_client()
    c.post("/api/notes", json={
        "id": "t1", "collection": "inbox", "title": "Ship it",
        "body": "", "signifier": "task", "status": "open",
    })
    stamp = c.patch("/api/notes/t1", json={"status": "complete"}).json()["completed"]
    again = c.patch("/api/notes/t1", json={"title": "Ship it properly"}).json()
    assert again["completed"] == stamp


def test_dragging_a_card_into_done_stamps_it(make_client):
    c = make_client()
    c.post("/api/notes", json={
        "id": "t2", "collection": "inbox", "title": "Card", "body": "",
        "signifier": "task", "status": "open",
    })
    c.post("/api/board/move", json={"id": "t2", "stage": "done"})
    assert c.get("/api/notes/t2").json()["completed"] == TODAY.isoformat()


def test_reopening_clears_the_date(make_client):
    """Otherwise finishing it again would report the first time forever."""
    c = make_client()
    c.post("/api/notes", json={
        "id": "t1", "collection": "inbox", "title": "Ship it",
        "body": "", "signifier": "task", "status": "open",
    })
    c.patch("/api/notes/t1", json={"status": "complete"})
    assert c.patch("/api/notes/t1", json={"status": "open"}).json()["completed"] is None


def test_dragging_a_card_back_out_clears_the_date(make_client):
    c = make_client()
    c.post("/api/notes", json={
        "id": "t2", "collection": "inbox", "title": "Card", "body": "",
        "signifier": "task", "status": "open",
    })
    c.post("/api/board/move", json={"id": "t2", "stage": "done"})
    c.post("/api/board/move", json={"id": "t2", "stage": "doing"})
    assert c.get("/api/notes/t2").json()["completed"] is None


def test_an_abandoned_task_is_not_stamped(make_client):
    """Struck through is not the same as finished."""
    c = make_client()
    c.post("/api/notes", json={
        "id": "t3", "collection": "inbox", "title": "Never mind", "body": "",
        "signifier": "task", "status": "irrelevant",
    })
    assert c.get("/api/notes/t3").json()["completed"] is None


# --- the endpoints --------------------------------------------------------

def test_daily_state_reports_the_day(make_client):
    c = make_client([_note("a", "Ship the zine", completed=YESTERDAY)])
    body = c.get(f"/api/daily/{YESTERDAY.isoformat()}").json()
    assert body["count"] == 1
    assert body["completed"][0]["title"] == "Ship the zine"
    assert body["has_note"] is False
    assert body["has_summary"] is False
    assert body["generated"] is False


def test_daily_notes_do_not_clutter_the_board(make_client):
    """A generated date page is not a task, so it must not become a card.

    Left alone, every day drops another date into To-do for the user to dismiss
    by hand, forever.
    """
    c = make_client([_note("a", "Real work", completed=YESTERDAY)])
    c.set_script([_chunk(content="A recap.")])
    c.post("/api/daily/summary", json={"date": YESTERDAY.isoformat()})

    board = c.get("/api/board").json()
    ids = [card["id"] for col in board["columns"] for card in col["cards"]]
    assert not [i for i in ids if i.startswith("daily-")], ids
    # ...but the board admits what it is holding back rather than looking smaller.
    assert board["hidden_daily"] == 1
    assert [card["id"] for col in board["columns"] for card in col["cards"]] == ["a"]


def test_a_normal_note_still_appears_on_the_board(make_client):
    """The guard is about generated daily notes, not about notes in general."""
    c = make_client([_note("a", "Real work", completed=YESTERDAY)])
    board = c.get("/api/board").json()
    ids = [card["id"] for col in board["columns"] for card in col["cards"]]
    assert "a" in ids


def test_bad_date_is_rejected(make_client):
    c = make_client()
    assert c.get("/api/daily/not-a-date").status_code == 400
    assert c.post("/api/daily/summary", json={"date": "nope"}).status_code == 400


def test_summary_writes_the_day_note(make_client):
    c = make_client([_note("a", "Ship the zine", completed=YESTERDAY)])
    c.set_script([_chunk(content="A short day. The zine went out.")])
    out = c.post("/api/daily/summary", json={"date": YESTERDAY.isoformat()}).json()
    assert out["wrote"] is True
    assert out["completed"] == 1
    assert out["titles"] == ["Ship the zine"]
    assert out["recap"] == "A short day. The zine went out."

    state = c.get(f"/api/daily/{YESTERDAY.isoformat()}").json()
    assert state["has_note"] is True
    assert state["has_summary"] is True
    assert state["generated"] is True

    written = next(c.vault_root.rglob(f"daily-{YESTERDAY.isoformat()}.md")).read_text()
    assert "Ship the zine" in written
    assert "A short day. The zine went out." in written


def test_summary_degrades_to_the_list_when_the_model_fails(make_client):
    """With the model down the record of the day still gets written."""
    c = make_client([_note("a", "Ship the zine", completed=YESTERDAY)])
    c.set_script([_chunk()], status=500)
    out = c.post("/api/daily/summary", json={"date": YESTERDAY.isoformat()}).json()
    assert out["wrote"] is True
    assert out["completed"] == 1
    assert out["recap"] is None
    assert "recap_error" in out
    written = next(c.vault_root.rglob(f"daily-{YESTERDAY.isoformat()}.md")).read_text()
    assert "Ship the zine" in written
    assert daily.MARK_START in written


def test_summary_creates_no_note_for_an_empty_day(make_client):
    """A page saying nothing happened is worse than no page."""
    c = make_client()
    out = c.post("/api/daily/summary", json={"date": YESTERDAY.isoformat()}).json()
    assert out["wrote"] is False
    assert out["completed"] == 0
    assert "reason" in out
    assert not list(c.vault_root.rglob("daily-*.md"))


def test_rerunning_keeps_what_the_person_wrote_in_the_note(make_client):
    """The reason the section is fenced rather than being the whole body."""
    c = make_client([_note("a", "Ship the zine", completed=YESTERDAY)])
    c.set_script([_chunk(content="First pass.")])
    c.post("/api/daily/summary", json={"date": YESTERDAY.isoformat()})

    note_id = f"daily-{YESTERDAY.isoformat()}"
    body = c.get(f"/api/notes/{note_id}").json()["body"]
    c.patch(f"/api/notes/{note_id}", json={"body": body + "\n\nNote to self: buy ink."})

    c.set_script([_chunk(content="Second pass.")])
    c.post(
        "/api/daily/summary",
        json={"date": YESTERDAY.isoformat(), "refresh": True},
    )

    after = c.get(f"/api/notes/{note_id}").json()["body"]
    assert "Note to self: buy ink." in after
    assert "Second pass." in after
    assert "First pass." not in after
    assert after.count(daily.MARK_START) == 1


def test_a_daily_note_is_not_a_recurrence_parent(tmp_path):
    """Making a daily note recur must not manufacture junk notes.

    `daily-2026-09-23` with `recurrence: daily` is read as a parent, so the next
    instance is named `daily-2026-09-23-2026-09-24` and lands beside it -- one
    more every day, forever. The app already makes a note per day, so the only
    correct number of instances is none.
    """
    db = Database(tmp_path / ".index.sqlite")
    dailynote = Note(
        id=daily.daily_note_id(TODAY),
        collection="daily",
        title=TODAY.isoformat(),
        body="",
        signifier=Signifier.NOTE,
        status=Status.COMPLETE,
        dates=[TODAY],
        completed=TODAY,
        recurrence="daily",
    )
    db.upsert(dailynote)
    assert db.run_recurring(TODAY + timedelta(days=1)) == []
    assert db.run_recurring(TODAY + timedelta(days=5)) == []


def test_a_normal_recurring_note_still_instantiates(tmp_path):
    """The guard must be about daily notes, not about recurrence."""
    db = Database(tmp_path / ".index.sqlite")
    real = Note(
        id="water-plants",
        collection="inbox",
        title="Water the plants",
        body="",
        signifier=Signifier.TASK,
        status=Status.OPEN,
        dates=[TODAY],
        recurrence="daily",
    )
    db.upsert(real)
    made = db.run_recurring(TODAY + timedelta(days=1))
    assert [n.id for n in made] == [f"water-plants-{(TODAY + timedelta(days=1)).isoformat()}"]


def test_a_summarised_day_is_not_summarised_again(make_client):
    """The ledger is what stops every tick spending a model call all evening."""
    c = make_client([_note("a", "Ship the zine", completed=YESTERDAY)])
    c.set_script([_chunk(content="First pass.")])
    c.post("/api/daily/summary", json={"date": YESTERDAY.isoformat()})

    pending = c.get("/api/daily").json()
    assert YESTERDAY.isoformat() in pending["summarised"]
    assert YESTERDAY.isoformat() not in pending["owed"]


def test_an_edit_made_while_the_model_thinks_is_not_clobbered(
    make_client, tmp_path, monkeypatch
):
    """The model call takes about a minute, and the note is written after it.

    Reading the body before the call and writing it back afterwards means an edit
    made during that minute is silently overwritten -- a data-loss window as wide
    as the generation itself, not the milliseconds a save usually has.
    """
    c = make_client([_note("a", "Real work", completed=YESTERDAY)])
    note_id = f"daily-{YESTERDAY.isoformat()}"

    # The race needs a note that already exists, which is the refresh case: a
    # first run creates it *after* the call, so there is nothing to clobber yet.
    c.set_script([_chunk(content="First pass.")])
    c.post("/api/daily/summary", json={"date": YESTERDAY.isoformat()})

    from app.llm import LlamaCpp

    async def complete_while_the_user_types(self, messages, **kwargs):
        path = next(tmp_path.rglob(f"{note_id}.md"))
        text = path.read_text()
        path.write_text(
            text.replace(daily.MARK_START, f"BUY INK\n\n{daily.MARK_START}", 1)
        )
        return "A recap."

    monkeypatch.setattr(LlamaCpp, "complete", complete_while_the_user_types)
    c.set_script([_chunk(content="A recap.")])
    c.post(
        "/api/daily/summary",
        json={"date": YESTERDAY.isoformat(), "refresh": True},
    )

    after = c.get(f"/api/notes/{note_id}").json()["body"]
    assert "BUY INK" in after, after
    assert "A recap." in after


def test_refresh_reruns_a_day_already_summarised(make_client):
    """The run happens once, so anything finished after the cutoff needs this."""
    c = make_client([_note("a", "Ship the zine", completed=YESTERDAY)])
    c.set_script([_chunk(content="First pass.")])
    c.post("/api/daily/summary", json={"date": YESTERDAY.isoformat()})

    c.set_script([_chunk(content="Second pass.")])
    out = c.post(
        "/api/daily/summary",
        json={"date": YESTERDAY.isoformat(), "refresh": True},
    ).json()
    assert out["recap"] == "Second pass."
