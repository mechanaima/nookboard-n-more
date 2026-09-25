"""Queries in a note: what they mean, and what they resolve to."""

from datetime import date

import pytest

from fastapi.testclient import TestClient

from app import query, weekly
from app.main import create_app
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


# --- done tasks: finished work, no period --------------------------------


def test_done_tasks_can_be_narrowed_to_one_collection():
    # The mirror of `open tasks in work`: same scope rules, opposite status.
    # A task marked complete is "done"; one left open or set aside is not.
    notes = [
        _task("a", "Ship zine", completed=THURSDAY, status=Status.COMPLETE, collection="work"),
        _task("b", "Buy ink", completed=THURSDAY, status=Status.COMPLETE, collection="journal"),
        _task("c", "Tidy desk", status=Status.OPEN, collection="work"),
    ]
    out = query.render("done tasks in work", notes, on=THURSDAY)
    assert "Ship zine" in out
    assert "Buy ink" not in out
    assert "Tidy desk" not in out


def test_done_tasks_can_be_narrowed_to_one_tag():
    # `#tag` routes to the tag path the same way `open tasks` does.
    notes = [
        Note(
            id="a", collection="school", title="Assignment 1", body="",
            signifier=Signifier.TASK, status=Status.COMPLETE,
            completed=THURSDAY, tags=["programming"],
        ),
        Note(
            id="b", collection="school", title="Lab 2", body="",
            signifier=Signifier.TASK, status=Status.COMPLETE,
            completed=THURSDAY, tags=["history"],
        ),
    ]
    out = query.render("done tasks in #programming", notes, on=THURSDAY)
    assert "Assignment 1" in out
    assert "Lab 2" not in out


def test_done_tasks_excludes_irrelevant_and_migrated():
    # `done` means complete, not closed. A task set aside was not finished.
    notes = [
        _task("a", "Ship zine", completed=THURSDAY, status=Status.COMPLETE, collection="work"),
        _task("b", "Later idea", status=Status.IRRELEVANT, collection="work"),
        _task("c", "Old job", status=Status.MIGRATED, collection="work"),
    ]
    out = query.render("done tasks in work", notes, on=THURSDAY)
    assert "Ship zine" in out
    assert "Later idea" not in out
    assert "Old job" not in out


def test_done_tasks_with_no_scope_lists_finished_work():
    # No `in <scope>`: same default pool as `open tasks`, just the other side.
    notes = [
        _task("a", "Ship zine", completed=THURSDAY, status=Status.COMPLETE, collection="work"),
        _task("b", "Tidy desk", status=Status.OPEN, collection="work"),
    ]
    out = query.render("done tasks", notes, on=THURSDAY)
    assert "Ship zine" in out
    assert "Tidy desk" not in out


def test_done_tasks_says_nothing_when_nothing_is_finished():
    # Empty answer is said, not blanked out, so the empty list does not look
    # like a query that failed. Pass an open task in the scope so `_named`
    # accepts the collection; `_by_status` then strips it out.
    notes = [_task("a", "Tidy desk", status=Status.OPEN, collection="work")]
    out = query.render("done tasks in work", notes, on=THURSDAY)
    assert "Nothing" in out
    assert "done" in out
    assert "work" in out


def test_done_tasks_refuses_a_named_collection_the_vault_lacks():
    # A named collection is validated before status filtering, so an empty vault
    # answers with the same "none" refusal shape as `open tasks in work`.
    out = query.render("done tasks in work", [], on=THURSDAY)
    assert "none" in out
    assert "work" in out


def test_done_tasks_refuses_an_unknown_tag_by_name():
    # The same refusal shape `_named` and `_tagged` use, so a typo does not
    # silently render as an empty list. `render` swallows `QueryError` so a
    # note still draws; the answer is the "not understood" marker it stands
    # in for.
    out = query.render("done tasks in #nope", [], on=THURSDAY)
    assert "Query not understood" in out
    assert "#nope" in out


def test_an_unknown_tag_suggests_the_tags_the_vault_has():
    # An empty vault can only say the tag is missing. Once the vault has tags,
    # the refusal names them, so a typo points at the near miss instead of
    # leaving you to guess what was meant.
    notes = [
        Note(
            id="a", collection="school", title="Assignment 1", body="",
            signifier=Signifier.TASK, status=Status.COMPLETE,
            completed=THURSDAY, tags=["programming"],
        ),
    ]
    out = query.render("done tasks in #nope", notes, on=THURSDAY)
    assert "no `#nope` tag" in out
    assert "#programming" in out


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


# --- `show notes in #tag`, or in a collection -----------------------------


def _tagged(nid, title, tags, *, created=THURSDAY, dates=(), collection="journal",
            signifier=Signifier.NOTE, status=Status.OPEN):
    return Note(
        id=nid, collection=collection, title=title, body="", tags=list(tags),
        dates=list(dates), signifier=signifier, status=status, created=created,
    )


def test_show_notes_in_a_tag_is_the_notes_carrying_it():
    notes = [
        _tagged("a", "One", ["alpha"]),
        _tagged("b", "Two", ["beta"]),
        _tagged("c", "Three", ["alpha", "beta"]),
    ]
    out = query.resolve(query.parse("show notes in #alpha"), notes, on=THURSDAY)
    assert "One" in out and "Three" in out
    assert "Two" not in out


def test_a_tag_is_matched_without_case():
    """`#ALPHA` and `#alpha` are one tag; nobody remembers which they typed."""
    notes = [_tagged("a", "One", ["alpha"])]
    assert "One" in query.resolve(query.parse("show notes in #ALPHA"), notes, on=THURSDAY)


def test_show_notes_in_a_collection_reads_exactly_like_naming_it():
    notes = [
        _tagged("a", "One", [], collection="journal"),
        _tagged("b", "Two", [], collection="school"),
    ]
    out = query.resolve(query.parse("show notes in school"), notes, on=THURSDAY)
    assert "Two" in out
    assert "One" not in out


def test_a_tag_this_vault_does_not_have_is_refused_with_the_ones_it_does():
    """A misspelled tag and an empty one look identical in a note."""
    notes = [_tagged("a", "One", ["alpha"]), _tagged("b", "Two", ["beta"])]
    with pytest.raises(query.QueryError) as err:
        query.resolve(query.parse("show notes in #alfa"), notes, on=THURSDAY)
    assert "#alpha" in str(err.value) and "#beta" in str(err.value)


def test_a_tag_on_a_note_the_default_view_hides_is_still_a_real_tag():
    """You named the set yourself, so it is answered exactly -- the same rule
    as naming a collection, which does not second-guess you either."""
    notes = [
        _tagged("t", "A shape for other notes", ["shop"], collection="templates"),
        _tagged("n", "A real note", ["shop"]),
    ]
    out = query.resolve(query.parse("show notes in #shop"), notes, on=THURSDAY)
    assert "A real note" in out
    assert "A shape for other notes" in out


def test_show_notes_alone_is_refused_because_it_is_every_note_you_own():
    with pytest.raises(query.QueryError) as err:
        query.parse("show notes")
    assert "which" in str(err.value)


def test_show_needs_the_word_notes():
    with pytest.raises(query.QueryError):
        query.parse("show books in #alpha")


def test_narrowing_by_a_tag_and_a_collection_at_once_is_refused():
    """Taking the last `in` would answer a different question than the note asks."""
    with pytest.raises(query.QueryError) as err:
        query.parse("show notes in #alpha in school")
    assert "not both" in str(err.value)


def test_a_bare_hash_does_not_name_a_tag():
    with pytest.raises(query.QueryError) as err:
        query.parse("show notes in #")
    assert "#mood" in str(err.value)


def _titles(markdown):
    return [ln.split("[[")[1].rstrip("]") for ln in markdown.splitlines()]


def test_newest_first_with_one_days_notes_come_out_by_title():
    """`created` is a date, so a day's notes tie; the tie-break must be the
    title ascending, not the whole comparison reversed."""
    notes = [
        _tagged("a", "Zebra", ["alpha"], created=MONDAY),
        _tagged("b", "Apple", ["alpha"], created=MONDAY),
        _tagged("c", "Newest", ["alpha"], created=SUNDAY),
    ]
    out = query.resolve(query.parse("show notes in #alpha"), notes, on=THURSDAY)
    assert _titles(out) == ["Newest", "Apple", "Zebra"]


def test_the_day_a_note_is_about_beats_the_day_it_was_made():
    """Two journal entries written in one sitting share `created`, so ordering by
    it alone puts last Monday above last Wednesday and calls that newest-first."""
    notes = [
        _tagged("a", "Monday entry", ["alpha"], created=THURSDAY, dates=[MONDAY]),
        _tagged("b", "Wednesday entry", ["alpha"], created=THURSDAY, dates=[MONDAY.replace(day=23)]),
    ]
    out = query.resolve(query.parse("show notes in #alpha"), notes, on=THURSDAY)
    assert _titles(out) == ["Wednesday entry", "Monday entry"]


def test_a_long_list_says_how_many_it_is_not_showing():
    notes = [_tagged(f"n{i}", f"Note {i:02d}", ["alpha"]) for i in range(query.SHOW_LIMIT + 3)]
    out = query.resolve(query.parse("show notes in #alpha"), notes, on=THURSDAY)
    lines = out.splitlines()
    assert len(lines) == query.SHOW_LIMIT + 1
    assert "3 more" in lines[-1]


def test_a_title_that_cannot_be_a_wikilink_is_listed_without_one():
    """A wikilink is followed by title, so a bracketed title would link elsewhere."""
    notes = [_tagged("a", "A [draft] title", ["alpha"])]
    out = query.resolve(query.parse("show notes in #alpha"), notes, on=THURSDAY)
    assert "[[" not in out
    assert "A [draft] title" in out


def test_an_empty_answer_says_so_rather_than_rendering_nothing():
    """A blank line where an answer belongs reads as a query that failed.

    Called directly because the parser cannot reach this today -- a tag that
    exists has notes, and a collection exists because a note is in it -- but the
    wording is what a person reads if either rule ever loosens, and "nothing" is
    a *different fact* from the refusal beside it.
    """
    out = query._show_markdown([], query.parse("show notes in #alpha"))
    assert out == "_Nothing in #alpha._"


def test_done_filters_to_the_notes_that_are_complete():
    notes = [
        _tagged("a", "Finished one", ["alpha"], status=Status.COMPLETE),
        _tagged("b", "Still going", ["alpha"]),
    ]
    out = query.resolve(query.parse("show notes in #alpha done"), notes, on=THURSDAY)
    assert _titles(out) == ["Finished one"]


def test_the_filter_does_not_care_which_case_you_write_it_in():
    notes = [_tagged("a", "Finished one", ["alpha"], status=Status.COMPLETE)]
    out = query.resolve(query.parse("show notes in #alpha DONE"), notes, on=THURSDAY)
    assert _titles(out) == ["Finished one"]


def test_not_done_is_everything_that_is_not_complete():
    """`irrelevant` and `migrated` were set aside, not finished. Counting them as
    done would quietly make this filter mean "closed" -- the board's word for a
    different question -- so the definition is pinned here."""
    notes = [
        _tagged("a", "Complete", ["alpha"], status=Status.COMPLETE),
        _tagged("b", "Open", ["alpha"], status=Status.OPEN),
        _tagged("c", "Irrelevant", ["alpha"], status=Status.IRRELEVANT),
        _tagged("d", "Migrated", ["alpha"], status=Status.MIGRATED),
    ]
    out = query.resolve(query.parse("show notes in #alpha not done"), notes, on=THURSDAY)
    assert _titles(out) == ["Irrelevant", "Migrated", "Open"]


def test_the_filter_works_on_a_collection_as_well_as_a_tag():
    notes = [
        _tagged("a", "Handed in", [], collection="school", status=Status.COMPLETE),
        _tagged("b", "Still going", [], collection="school"),
    ]
    out = query.resolve(query.parse("show notes in school not done"), notes, on=THURSDAY)
    assert _titles(out) == ["Still going"]


def test_a_filter_needs_a_scope_to_filter():
    with pytest.raises(query.QueryError) as err:
        query.parse("show notes done")
    assert "which notes" in str(err.value)


def test_a_filter_on_a_verb_that_already_means_it_is_refused():
    """`open tasks` is only the ones that are not done, so the word is redundant
    rather than unknown -- and a shrug would be the wrong answer to give."""
    for text in ("open tasks done", "completed this week not done"):
        with pytest.raises(query.QueryError) as err:
            query.parse(text)
        assert "already means that" in str(err.value)


def test_the_empty_line_says_which_filter_was_applied():
    notes = [_tagged("a", "Still going", ["alpha"])]
    out = query.resolve(query.parse("show notes in #alpha done"), notes, on=THURSDAY)
    assert out == "_Nothing in #alpha that is done._"


def test_the_query_says_what_you_can_write():
    with pytest.raises(query.QueryError) as err:
        query.parse("show notes sideways")
    assert "show notes in #mood" in str(err.value)


# --- the same thing over HTTP --------------------------------------------


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    with TestClient(create_app(vault_root=root)) as client:
        yield client


def _add(vault, nid, collection, title, tags=()):
    resp = vault.post("/api/notes", json={
        "id": nid, "collection": collection, "title": title, "body": "",
        "signifier": "note", "status": "open", "tags": list(tags),
    })
    assert resp.status_code in (200, 201), resp.text


def test_a_tag_query_over_the_api(vault):
    """`q` is the whole contract with the browser: it sends the fence's text and
    gets markdown back, so the client never learns a second syntax."""
    _add(vault, "q-one", "journal", "Tagged one", ["alpha"])
    _add(vault, "q-two", "journal", "Not tagged", ["beta"])
    body = vault.get("/api/query", params={"q": "show notes in #alpha"}).json()
    assert body["query"] == "show notes in #alpha"
    assert "Tagged one" in body["markdown"]
    assert "Not tagged" not in body["markdown"]


def test_a_refusal_is_a_sentence_over_the_api_too(vault):
    body = vault.get("/api/query", params={"q": "show notes in #nope"}).json()
    assert "Query not understood" in body["markdown"]
    assert "no `#nope` tag" in body["markdown"]
