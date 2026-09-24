"""The dashboard: what each card shows, and that it cannot contradict a view."""

from datetime import date

from app import daily, deps, home, mood as moodlib
from app.models import Note, Signifier, Status

# 2026-09-21 is the Monday of ISO week 2026-W39; 2026-09-24 is the Thursday.
THURSDAY = date.fromisocalendar(2026, 39, 4)


def _task(nid, title, *, status=Status.OPEN, completed=None, collection="journal", tags=()):
    return Note(
        id=nid, collection=collection, title=title, body="",
        signifier=Signifier.TASK, status=status, completed=completed,
        dates=[], tags=list(tags),
    )


def _note(nid, title, *, collection="journal", dates=(), tags=(), mood=None, pain=None):
    return Note(
        id=nid, collection=collection, title=title, body="",
        signifier=Signifier.NOTE, status=Status.OPEN,
        dates=list(dates), tags=list(tags), mood=mood, pain=pain,
    )


# --- speed dials ----------------------------------------------------------

def test_the_vault_is_named_by_its_folder():
    assert home.vault_name("/home/someone/vaults/School") == "School"
    assert home.vault_name("/") == "vault"


# --- statistics -----------------------------------------------------------

def _board(notes):
    """The board's own numbers, filtered the way the board view filters."""
    workable = [n for n in notes if home.is_work(n)]
    return deps.board_summary(workable, deps.index_by_id(workable))


def test_statistics_counts_what_is_there():
    notes = [
        _task("a", "Open thing"),
        _task("b", "Done thing", status=Status.COMPLETE, completed=THURSDAY),
        _note("c", "Just a note", tags=["zine", "work"]),
    ]
    stats = home.statistics(notes)
    assert stats["notes"] == 3
    assert stats["tasks"] == 2  # the signifier, not the status: done work is work
    assert stats["tags"] == 2
    assert set(stats) == {"notes", "tasks", "tags", "collections"}


def test_the_work_counts_live_only_on_the_board_card():
    # Two cards showing "open" is how one of them ends up disagreeing: the
    # version of this card that counted open itself said 2 where the board said
    # 3, because the board counts anything not closed -- notes included -- and
    # that count had quietly decided "open" meant open tasks. So the tile is not
    # here at all; the board card copies the board's own summary.
    notes = [_task("a", "Open thing"), _note("d", "A note")]
    assert "open" not in home.statistics(notes)
    out = home.summary(notes, root="/tmp/v", today=THURSDAY, calendar_counts={})
    assert out["statistics"]["notes"] == 2
    assert out["board"]["open"] == 2  # the task and the note, as the board sees them


def test_shapes_and_generated_notes_are_files_but_not_work():
    # A template is a shape and a daily note is a record, so neither is work --
    # but both are files in the vault, and the file count should say so.
    notes = [
        _task("tpl-shop", "Weekly shop", collection="templates"),
        _task("daily-2026-09-24", "2026-09-24", collection="daily"),
        _task("a", "Real thing"),
    ]
    out = home.summary(notes, root="/tmp/v", today=THURSDAY, calendar_counts={})
    assert out["statistics"]["notes"] == 3   # three files
    assert out["board"]["total"] == 1        # one of them is work


def test_the_collection_count_comes_from_the_real_list_when_given_one():
    # `/api/collections` counts an empty templates folder; the notes cannot.
    notes = [_note("a", "One")]
    assert home.statistics(notes)["collections"] == 1
    got = home.statistics(notes, collections=["inbox", "templates"])
    assert got["collections"] == 2


# --- the calendar card ----------------------------------------------------

def test_the_month_carries_what_the_grid_needs_to_lay_it_out():
    card = home.calendar_card({}, month=date(2026, 9, 1), today=THURSDAY)
    assert card["year"] == 2026
    assert card["month"] == 9
    assert card["label"] == "September 2026"
    assert card["today"] == "2026-09-24"
    # Deliberately no days_in_month and no first_weekday. monthGrid() in
    # calendar.js lays the month out from the year and month alone, and a second
    # answer to "which weekday does the 1st fall on" is one that can be wrong --
    # the deleted field here claimed Monday was day 0 while the grid, which uses
    # getDay(), counts from Sunday.
    assert set(card) == {"year", "month", "label", "today", "counts"}


def test_today_is_only_marked_in_the_month_being_shown():
    card = home.calendar_card({}, month=date(2026, 8, 1), today=THURSDAY)
    assert card["today"] is None


def test_only_the_shown_months_counts_come_through():
    counts = {"2026-09-24": 3, "2026-09-01": 1, "2026-08-31": 9}
    card = home.calendar_card(counts, month=date(2026, 9, 1), today=THURSDAY)
    assert card["counts"] == {"2026-09-24": 3, "2026-09-01": 1}


# --- today ----------------------------------------------------------------

def test_today_knows_whether_the_day_has_been_written_up():
    day = daily.note_for(THURSDAY, [], "some prose")
    notes = [day, _task("a", "Thing", status=Status.COMPLETE, completed=THURSDAY)]
    card = home.today_card(notes, THURSDAY)
    assert card["has_note"] is True
    assert card["summarised"] is True
    assert card["finished"] == 1
    assert card["titles"] == ["Thing"]


def test_today_with_nothing_yet():
    card = home.today_card([], THURSDAY)
    assert card == {
        "date": "2026-09-24", "has_note": False, "summarised": False,
        "finished": 0, "titles": [],
    }


def test_a_note_that_carries_the_id_but_has_not_been_summarised():
    blank = Note(
        id=daily.daily_note_id(THURSDAY), collection="daily", title="2026-09-24",
        body="just what I typed", signifier=Signifier.NOTE, status=Status.OPEN,
    )
    assert home.today_card([blank], THURSDAY)["summarised"] is False


# --- mood -----------------------------------------------------------------

def test_the_mood_card_reports_the_streak_and_todays_reading():
    notes = [
        _note("m1", "check in", dates=[date(2026, 9, 23)], tags=["mood"], mood="great", pain=6),
        _note("m2", "check in", dates=[THURSDAY], tags=["mood"], mood="good", pain=4),
    ]
    card = home.mood_card(notes, THURSDAY)
    assert card["streak"] == 2
    assert card["days_logged"] == 2
    assert card["logged_today"] is True
    assert card["level"] == "good"
    assert card["pain"] == 4


def test_the_mood_card_is_quiet_when_nothing_has_been_logged():
    card = home.mood_card([], THURSDAY)
    assert card == {
        "streak": 0, "days_logged": 0, "logged_today": False,
        "level": None, "pain": None, "most_common": None,
    }


# --- the whole thing ------------------------------------------------------

def test_the_summary_carries_every_card():
    out = home.summary(
        [], root="/tmp/SomeVault", today=THURSDAY, calendar_counts={}, collections=["inbox"]
    )
    assert out["vault"] == "SomeVault"
    assert out["today"] == "2026-09-24"
    assert set(out) == {
        "vault", "today", "calendar", "statistics", "board", "today_card", "mood",
        "workspaces",
    }
    # No states were handed over, so the card says there is nothing to show --
    # rather than inventing a count for folders nobody read.
    assert out["workspaces"]["total"] == 0
    assert out["workspaces"]["line"] == "no workspaces yet"


def test_the_workspaces_card_is_built_from_the_states_it_is_handed():
    states = [
        {"note_title": "Settled", "uncommitted": 0, "reasons": [], "markers": []},
        {"note_title": "Busy", "uncommitted": 4, "reasons": ["4 uncommitted"],
         "markers": []},
    ]
    out = home.summary(
        [], root="/tmp/v", today=THURSDAY, calendar_counts={}, workspace_states=states
    )
    card = out["workspaces"]
    assert card["total"] == 2
    assert card["need_attention"] == 1
    assert card["line"] == "1 workspace needs attention"
    # The same order the view shows, so the card's first line is the view's first
    # card: the thing that wants you.
    assert [w["note_title"] for w in card["workspaces"]] == ["Busy", "Settled"]


def test_the_cards_agree_with_the_views_they_stand_for():
    # The point of the module: the dashboard is a view of the vault, so its
    # numbers have to be the numbers the other views produce -- to the count.
    notes = [
        _task("a", "Open thing"),
        _task("b", "Blocked thing"),
        _task("c", "Done thing", status=Status.COMPLETE, completed=THURSDAY),
        _note("d", "A note"),
    ]
    notes[1] = Note(
        id="b", collection="journal", title="Blocked thing", body="",
        signifier=Signifier.TASK, status=Status.OPEN, blocked_by=["a"],
    )
    out = home.summary([*notes], root="/tmp/v", today=THURSDAY, calendar_counts={})
    assert out["board"] == _board(notes)
    assert out["today_card"]["finished"] == len(daily.completed_on(notes, THURSDAY))
    # and the mood card is the mood layer's own summary, not a second opinion
    series = moodlib.daily_series(notes, start=THURSDAY.replace(day=1), end=THURSDAY)
    assert out["mood"]["streak"] == moodlib.summarize(series, today=THURSDAY)["streak"]
