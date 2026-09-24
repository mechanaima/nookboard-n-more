"""Queries in a note: what they mean, and what they resolve to."""

from datetime import date

import pytest

from app import query, weekly
from app.models import Note, Signifier, Status

# 2026-09-21 is the Monday of ISO week 2026-W39; 2026-09-24 is the Thursday.
MONDAY = date.fromisocalendar(2026, 39, 1)
THURSDAY = MONDAY.replace(day=24)
SUNDAY = MONDAY.replace(day=27)


def _task(
    nid,
    title,
    *,
    completed=None,
    status=Status.OPEN,
    signifier=Signifier.TASK,
    collection="journal",
):
    return Note(
        id=nid,
        collection=collection,
        title=title,
        body="",
        signifier=signifier,
        status=status,
        completed=completed,
    )


# --- reading a query ------------------------------------------------------

def test_the_three_things_a_query_can_ask():
    assert query.parse("days this week").verb == "days"
    assert query.parse("completed this week").verb == "completed"
    assert query.parse("open tasks").verb == "open"
    assert query.parse("open").verb == "open"


def test_a_query_is_read_forgivingly_about_whitespace_and_case():
    got = query.parse("  Completed   THIS   Week ")
    assert got.verb == "completed"
    # Whitespace is collapsed, but the words are kept as written: the period is
    # echoed back at you, so it has to be spelled the way you spelled it.
    assert got.period == "THIS Week"


def test_case_does_not_change_what_a_query_covers():
    # The verb and the period are matched case-insensitively, which is the part
    # that matters -- `THIS WEEK` has to mean the same days as `this week`.
    for text in ("completed this week", "Completed This Week", "COMPLETED THIS WEEK"):
        q = query.parse(text)
        assert query.bounds(q.period, THURSDAY) == query.bounds("this week", THURSDAY)
    assert query.bounds(query.parse("DAYS TODAY").period, THURSDAY) == (THURSDAY, THURSDAY)


def test_an_iso_week_keeps_its_capital_W_when_quoted_back():
    # `in 2026-w01` is not how anyone writes a week, and it is the query's own
    # text that ends up in the note when nothing was finished.
    q = query.parse("completed in 2026-W01")
    assert q.period == "in 2026-W01"
    empty = query.resolve(q, [], on=THURSDAY)
    assert "in in" not in empty
    assert "2026-W01" in empty


def test_a_query_that_cannot_be_read_says_so():
    for bad in ("", "   ", "days", "completed", "count my things", "open the pod bay doors"):
        with pytest.raises(query.QueryError):
            query.parse(bad)


# --- narrowing to a collection --------------------------------------------


def test_open_tasks_can_be_narrowed_to_one_collection():
    notes = [
        _task("a", "Fix printer", collection="work"),
        _task("b", "Buy ink", collection="journal"),
    ]
    out = query.render("open tasks in work", notes, on=THURSDAY)
    assert "Fix printer" in out
    assert "Buy ink" not in out


def test_a_collection_is_matched_regardless_of_case():
    notes = [_task("a", "Fix printer", collection="work")]
    assert "Fix printer" in query.render("open tasks in WORK", notes, on=THURSDAY)


def test_the_collection_comes_back_with_its_own_spelling():
    # As with an ISO week: the vault's spelling is the one that is real.
    got = query.parse("open tasks in Work")
    assert got.collection == "Work"


def test_completed_can_be_narrowed_to_one_collection():
    notes = [
        _task("a", "Ship zine", completed=THURSDAY, status=Status.COMPLETE, collection="work"),
        _task("b", "Buy ink", completed=THURSDAY, status=Status.COMPLETE, collection="journal"),
    ]
    out = query.render("completed this week in work", notes, on=THURSDAY)
    assert "Ship zine" in out
    assert "Buy ink" not in out


def test_in_an_iso_week_is_still_a_period_and_never_a_collection():
    # The whole reason `_split_scope` looks at what follows `in`.
    got = query.parse("completed in 2026-W39")
    assert (got.period, got.collection) == ("in 2026-W39", None)
    out = query.render("completed in 2026-W39", [], on=THURSDAY)
    assert "collection" not in out


def test_an_unknown_collection_is_refused_and_names_the_ones_that_exist():
    notes = [_task("a", "Fix printer", collection="work")]
    out = query.render("open tasks in wrok", notes, on=THURSDAY)
    assert "wrok" in out and "`work`" in out
    assert "Fix printer" not in out


def test_a_named_collection_in_an_empty_vault_says_so():
    out = query.render("open tasks in work", [], on=THURSDAY)
    assert "none" in out


def test_days_refuses_a_collection_rather_than_quietly_ignoring_it():
    notes = [_task("a", "Fix printer", collection="work")]
    out = query.render("days this week in work", notes, on=THURSDAY)
    assert "collection" in out
    assert "Monday" not in out


def test_open_tasks_left_alone_leaves_out_the_shapes_and_the_generated_notes():
    # A template is a shape for other notes and a period note is something the
    # app wrote, so neither is a thing to be doing -- the same set the board
    # hides. Without this, a task-shaped template reads as outstanding work.
    template = Note(
        id="tpl-week", collection="templates", title="Weekly shop", body="",
        signifier=Signifier.TASK, status=Status.OPEN,
    )
    generated = Note(
        id="daily-2026-09-24", collection="daily", title="2026-09-24", body="",
        signifier=Signifier.TASK, status=Status.OPEN,
    )
    out = query.render("open tasks", [template, generated, _task("a", "Fix printer")], on=THURSDAY)
    assert "Fix printer" in out
    assert "Weekly shop" not in out
    assert "2026-09-24" not in out


def test_naming_one_of_those_by_name_still_answers():
    # Hiding a shape from the default view is not the same as forbidding it.
    template = Note(
        id="tpl-week", collection="templates", title="Weekly shop", body="",
        signifier=Signifier.TASK, status=Status.OPEN,
    )
    out = query.render("open tasks in templates", [template, _task("a", "Fix printer")], on=THURSDAY)
    assert "Weekly shop" in out
    assert "Fix printer" not in out


def test_a_stray_word_after_open_is_not_read_as_a_collection():
    # `in` is required, so a typo is blamed on the keyword rather than reported
    # as a collection the vault does not have.
    for bad in ("open tas", "open tasks in"):
        out = query.render(bad, [], on=THURSDAY)
        assert "open tasks in work" in out, bad


# --- what a period covers -------------------------------------------------

def test_named_periods():
    assert query.bounds("today", THURSDAY) == (THURSDAY, THURSDAY)
    assert query.bounds("yesterday", THURSDAY) == (THURSDAY.replace(day=23),) * 2
    assert query.bounds("this week", THURSDAY) == (MONDAY, SUNDAY)
    assert query.bounds("last week", THURSDAY) == (MONDAY.replace(day=14), SUNDAY.replace(day=20))


def test_months():
    assert query.bounds("this month", THURSDAY) == (THURSDAY.replace(day=1), THURSDAY.replace(day=30))
    # September's predecessor is August, with its own 31 days.
    assert query.bounds("last month", THURSDAY) == (date(2026, 8, 1), date(2026, 8, 31))


def test_an_explicit_day_or_week():
    assert query.bounds("on 2026-01-02", THURSDAY) == (date(2026, 1, 2),) * 2
    assert query.bounds("in 2026-W39", THURSDAY) == (MONDAY, SUNDAY)


def test_a_bad_period_is_refused_with_something_useful_to_do_instead():
    """An error in a note is read at the moment you are already confused.

    So it has to name something you could actually type, not just refuse.
    """
    for bad in ("on tuesday", "in 2026-39", "sometime", ""):
        with pytest.raises(query.QueryError) as exc:
            query.bounds(bad, THURSDAY)
        message = str(exc.value)
        assert "`" in message, f"no example offered: {message}"

    # and when it was given something, it says what it choked on
    with pytest.raises(query.QueryError) as exc:
        query.bounds("sometime", THURSDAY)
    assert "sometime" in str(exc.value)


def test_a_period_is_relative_to_the_note_not_the_clock():
    """The point of passing a reference day in.

    A note written in week 38 and reopened in March must still say week 38 --
    otherwise an old note quietly rewrites its own history.
    """
    in_w38 = date.fromisocalendar(2026, 38, 3)
    assert query.bounds("this week", in_w38) == weekly.week_bounds("2026-W38")
    assert query.bounds("last week", in_w38) == weekly.week_bounds("2026-W37")


# --- the days of a span ---------------------------------------------------

def test_days_are_links_with_their_weekday_names():
    out = query.render("days this week", [], on=THURSDAY)
    lines = out.split("\n")
    assert len(lines) == 7
    assert lines[0] == "- [[2026-09-21]] Monday"
    assert lines[6] == "- [[2026-09-27]] Sunday"


def test_days_for_a_single_day():
    assert query.render("days today", [], on=THURSDAY) == "- [[2026-09-24]] Thursday"


# --- finished work --------------------------------------------------------

def test_one_day_is_a_plain_list():
    notes = [_task("a", "Fix printer", completed=THURSDAY, status=Status.COMPLETE)]
    out = query.render("completed today", notes, on=THURSDAY)
    assert out == "- [[Fix printer]]"


def test_a_span_is_grouped_by_the_day_each_thing_was_finished():
    notes = [
        _task("a", "Fix printer", completed=MONDAY, status=Status.COMPLETE),
        _task("b", "Ship zine", completed=THURSDAY, status=Status.COMPLETE),
    ]
    out = query.render("completed this week", notes, on=THURSDAY)
    assert out.split("\n") == [
        "- **Mon 21** — [[Fix printer]]",
        "- **Thu 24** — [[Ship zine]]",
    ]


def test_the_same_day_s_work_shares_a_line():
    notes = [
        _task("a", "Fix printer", completed=THURSDAY, status=Status.COMPLETE),
        _task("b", "Ship zine", completed=THURSDAY, status=Status.COMPLETE),
    ]
    out = query.render("completed this week", notes, on=THURSDAY)
    assert out == "- **Thu 24** — [[Fix printer]], [[Ship zine]]"


def test_nothing_finished_is_said_rather_than_left_blank():
    out = query.render("completed this week", [], on=THURSDAY)
    assert "Nothing finished" in out


def test_appearing_does_not_mean_finished():
    """A note created this week but never completed is not this week's work."""
    notes = [_task("a", "Someday", status=Status.OPEN)]
    assert "Nothing finished" in query.render("completed this week", notes, on=THURSDAY)


def test_abandoned_is_not_finished():
    notes = [_task("a", "Gave up", status=Status.IRRELEVANT, completed=None)]
    assert "Nothing finished" in query.render("completed this week", notes, on=THURSDAY)


def test_a_query_agrees_with_the_weekly_rollup_about_the_same_week():
    """Two renderings of one week must list the same things.

    They share `daily.completed_on`, so they cannot disagree about what counts as
    done; this pins that they also agree about which days are in the week.
    """
    notes = [
        _task("a", "Fix printer", completed=MONDAY, status=Status.COMPLETE),
        _task("b", "Ship zine", completed=SUNDAY, status=Status.COMPLETE),
        _task("c", "Later", status=Status.OPEN),
    ]
    from_query = query.completed_by_day(notes, *query.bounds("this week", THURSDAY))
    from_rollup = weekly.finished_in(notes, weekly.week_key(THURSDAY))
    assert from_query == from_rollup


def test_a_generated_note_never_counts_as_the_period_s_own_work():
    daily_note = Note(
        id="daily-2026-09-24",
        collection="daily",
        title="2026-09-24",
        body="",
        status=Status.COMPLETE,
        completed=THURSDAY,
    )
    out = query.render("completed today", [daily_note], on=THURSDAY)
    assert "Nothing finished" in out


# --- open work ------------------------------------------------------------

def test_open_tasks_are_listed():
    notes = [
        _task("a", "Zebra", status=Status.OPEN),
        _task("b", "Apple", status=Status.OPEN),
        _task("c", "Done", status=Status.COMPLETE),
        Note(id="d", collection="inbox", title="Just a note", body="", status=Status.OPEN),
    ]
    assert query.render("open tasks", notes, on=THURSDAY) == "- [[Apple]]\n- [[Zebra]]"


def test_nothing_open_is_said():
    assert query.render("open tasks", [], on=THURSDAY) == "_Nothing open._"


# --- a query that cannot be read ------------------------------------------

def test_an_unreadable_query_is_shown_in_the_note_rather_than_raising():
    out = query.render("count my things", [], on=THURSDAY)
    assert "count my things" in out
    assert "Query not understood" in out


# --- finding the blocks in a note -----------------------------------------

def test_blocks_are_found_and_replaced():
    body = "Before\n\n```nookboard\ncompleted today\n```\n\nAfter\n"
    notes = [_task("a", "Fix printer", completed=THURSDAY, status=Status.COMPLETE)]
    out = query.render_body(body, notes, on=THURSDAY)
    assert out == "Before\n\n- [[Fix printer]]\n\nAfter\n"


def test_a_fence_in_another_language_is_left_alone():
    """Only our language is a query; the rest is code you wanted to show."""
    body = "```python\nprint('hi')\n```\n\n```nookboard\nopen tasks\n```\n"
    out = query.render_body(body, [], on=THURSDAY)
    assert "print('hi')" in out
    assert "_Nothing open._" in out


def test_several_blocks_in_one_note():
    body = (
        "## Days\n\n```nookboard\ndays today\n```\n\n"
        "## Done\n\n```nookboard\ncompleted today\n```\n"
    )
    out = query.render_body(body, [], on=THURSDAY)
    assert "- [[2026-09-24]] Thursday" in out
    assert "Nothing finished" in out


def test_a_note_with_no_blocks_is_unchanged():
    body = "Just writing.\n\n```js\nconst a = 1;\n```\n"
    assert query.render_body(body, [], on=THURSDAY) == body


def test_find_blocks_reports_where_they_are():
    body = "x\n```nookboard\ndays today\n```\ny"
    found = query.find_blocks(body)
    assert len(found) == 1
    start, end, text = found[0]
    assert body[start:end].startswith("```nookboard")
    assert text == "days today"
