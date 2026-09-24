"""TDD for app.deps — the pure dependency graph and board ordering.

These are the rules the whole task-management feature rests on, so they are
tested directly rather than only through the API.
"""
from __future__ import annotations

from datetime import date

import pytest

from app import deps
from app.models import Note, Stage, Status, stage_for_status, reconcile


def mk(note_id: str, *, status=Status.OPEN, stage=None, blocked_by=(), position=None,
       created=date(2026, 1, 1), title=None, signifier="task") -> Note:
    return Note(
        id=note_id,
        collection="inbox",
        title=title or note_id,
        body="",
        signifier=signifier,
        status=status,
        stage=stage,
        blocked_by=list(blocked_by),
        position=position,
        created=created,
    )


# -- blocked-ness ------------------------------------------------------------


def test_open_blocker_blocks():
    blocker = mk("b")
    dep = mk("a", blocked_by=["b"])
    by_id = deps.index_by_id([blocker, dep])
    assert deps.is_blocked(dep, by_id) is True


def test_completed_blocker_does_not_block():
    blocker = mk("b", status=Status.COMPLETE)
    dep = mk("a", blocked_by=["b"])
    by_id = deps.index_by_id([blocker, dep])
    assert deps.is_blocked(dep, by_id) is False


def test_dropped_blocker_does_not_block():
    """A task you decided not to do must not hold up the thing behind it."""
    blocker = mk("b", status=Status.IRRELEVANT)
    dep = mk("a", blocked_by=["b"])
    by_id = deps.index_by_id([blocker, dep])
    assert deps.is_blocked(dep, by_id) is False


def test_missing_blocker_still_blocks_and_is_reported():
    """Deleting a blocker must not silently open the gate it was holding."""
    dep = mk("a", blocked_by=["ghost"])
    by_id = deps.index_by_id([dep])
    assert deps.is_blocked(dep, by_id) is True
    (resolved,) = deps.resolve(dep.blocked_by, by_id)
    assert resolved["missing"] is True
    assert resolved["closed"] is False


def test_resolve_reports_titles_and_closed_state():
    blocker = mk("b", status=Status.COMPLETE, title="Ship it")
    dep = mk("a", blocked_by=["b"])
    by_id = deps.index_by_id([blocker, dep])
    assert deps.resolve(dep.blocked_by, by_id) == [
        {"id": "b", "title": "Ship it", "status": "complete", "stage": "done",
         "closed": True, "missing": False}
    ]


def test_blocking_is_the_derived_reverse_edge():
    a = mk("a", blocked_by=["b"])
    b = mk("b")
    assert [n.id for n in deps.blocking("b", [a, b])] == ["a"]
    assert deps.blocking("a", [a, b]) == []


def test_a_note_cannot_block_itself(  ):
    a = mk("a", blocked_by=["a"])
    by_id = deps.index_by_id([a])
    assert deps.blocking("a", [a]) == []
    assert deps.bottleneck_counts([a]) == {}


def test_bottleneck_counts_counts_dependents():
    a = mk("a", blocked_by=["c"])
    b = mk("b", blocked_by=["c"])
    c = mk("c")
    assert deps.bottleneck_counts([a, b, c]) == {"c": 2}


# -- cycle prevention --------------------------------------------------------


def test_self_dependency_is_a_cycle():
    assert deps.find_cycle("a", "a", {}) == ["a", "a"]


def test_direct_two_cycle_is_caught():
    """b already waits on a, so a waiting on b would deadlock both."""
    a = mk("a")
    b = mk("b", blocked_by=["a"])
    cycle = deps.find_cycle("a", "b", deps.index_by_id([a, b]))
    assert cycle == ["a", "b", "a"]


def test_transitive_three_cycle_is_caught():
    a = mk("a")
    b = mk("b", blocked_by=["a"])
    c = mk("c", blocked_by=["b"])
    cycle = deps.find_cycle("a", "c", deps.index_by_id([a, b, c]))
    assert cycle == ["a", "c", "b", "a"]


def test_diamond_is_not_a_cycle():
    """Two paths to the same goal is normal project shape, not a loop."""
    d = mk("d")
    b = mk("b", blocked_by=["d"])
    c = mk("c", blocked_by=["d"])
    a = mk("a", blocked_by=["b", "c"])
    by_id = deps.index_by_id([a, b, c, d])
    assert deps.find_cycle("a", "d", by_id) is None
    assert deps.find_cycle("b", "c", by_id) is None


def test_check_blockers_returns_first_offending_chain():
    a = mk("a")
    b = mk("b", blocked_by=["a"])
    by_id = deps.index_by_id([a, b])
    assert deps.check_blockers("a", ["b"], by_id) == ["a", "b", "a"]
    assert deps.check_blockers("a", ["zzz"], by_id) is None


def test_normalize_drops_self_and_duplicates():
    by_id = {}
    assert deps.normalize_blocked_by(["a", "a", "", "  ", "b", "b"], note_id="a", by_id=by_id) == ["b"]


def test_normalize_keeps_unknown_ids():
    """A dependency on a note that is not indexed yet is still a dependency."""
    assert deps.normalize_blocked_by(["not-yet"], note_id="a", by_id={}) == ["not-yet"]


# -- columns and status reconciliation --------------------------------------


@pytest.mark.parametrize("status,expected", [
    (Status.OPEN, Stage.TODO),
    (Status.MIGRATED, Stage.TODO),
    (Status.SCHEDULED, Stage.TODO),
    (Status.COMPLETE, Stage.DONE),
    (Status.IRRELEVANT, Stage.DONE),
])
def test_stage_is_derived_from_status_when_unplaced(status, expected):
    assert stage_for_status(status) is expected


def test_explicit_stage_wins_over_derived():
    n = mk("a", stage="backlog", status=Status.OPEN)
    assert deps.stage_of(n) == "backlog"


def test_moving_a_card_to_done_completes_the_task():
    stage, status = reconcile(Stage.DONE.value, Status.OPEN)
    assert (stage, status) == ("done", Status.COMPLETE)


def test_completing_a_task_moves_its_card_to_done():
    stage, status = reconcile(Stage.DOING.value, Status.COMPLETE)
    assert (stage, status) == ("done", Status.COMPLETE)


def test_completing_an_unplaced_task_leaves_stage_derived():
    """Nothing forces an explicit column, so untouched notes stay clean."""
    stage, status = reconcile(None, Status.COMPLETE)
    assert stage is None
    assert status is Status.COMPLETE


def test_an_unplaced_open_task_keeps_no_explicit_stage():
    assert reconcile(None, Status.OPEN) == (None, Status.OPEN)


def test_dropped_task_does_not_get_dragged_out_of_done():
    stage, status = reconcile(Stage.DONE.value, Status.IRRELEVANT)
    assert (stage, status) == ("done", Status.IRRELEVANT)


# -- column ordering ---------------------------------------------------------


def test_positioned_cards_sort_before_unpositioned_ones():
    a = mk("a", position=None)
    b = mk("b", position=1.0)
    assert [n.id for n in deps.sort_column([a, b])] == ["b", "a"]


def test_cards_sort_by_position_then_creation():
    c = mk("c", position=2.0, created=date(2026, 1, 1))
    a = mk("a", position=1.0, created=date(2026, 5, 1))
    b = mk("b", position=1.0, created=date(2026, 1, 9))
    assert [n.id for n in deps.sort_column([c, a, b])] == ["b", "a", "c"]


def test_position_zero_is_respected_not_treated_as_absent():
    a = mk("a", position=0.0)
    b = mk("b", position=None)
    assert [n.id for n in deps.sort_column([b, a])] == ["a", "b"]


def test_plan_move_inserts_before_a_named_card_and_renumbers():
    a = mk("a", position=1.0)
    b = mk("b", position=2.0)
    m = mk("m")
    assert deps.plan_move([a, b], m, "b") == {"m": 2.0, "b": 3.0}


def test_plan_move_to_the_end_when_before_id_is_none():
    a = mk("a", position=1.0)
    b = mk("b", position=2.0)
    m = mk("m")
    assert deps.plan_move([a, b], m, None) == {"m": 3.0}


def test_plan_move_into_an_empty_column():
    assert deps.plan_move([], mk("m"), None) == {"m": 1.0}


def test_plan_move_repairs_cards_that_were_never_placed():
    """First drop in a column seeds clean integers for everything in it."""
    a = mk("a", position=None)
    b = mk("b", position=None)
    m = mk("m")
    assert deps.plan_move([a, b], m, "a") == {"m": 1.0, "a": 2.0, "b": 3.0}


def test_plan_move_touches_nothing_that_does_not_move():
    a = mk("a", position=1.0)
    b = mk("b", position=2.0)
    m = mk("m")
    # Dropping m at the end leaves a and b exactly where they were.
    assert set(deps.plan_move([a, b], m, None)) == {"m"}


def test_plan_move_appends_when_before_id_is_unknown():
    a = mk("a", position=1.0)
    m = mk("m")
    assert deps.plan_move([a], m, "not-a-card") == {"m": 2.0}


# -- summary -----------------------------------------------------------------


def test_board_summary_separates_ready_from_blocked():
    blocker = mk("b")
    blocked = mk("c", blocked_by=["b"])
    done = mk("d", status=Status.COMPLETE)
    notes = [blocker, blocked, done]
    by_id = deps.index_by_id(notes)
    assert deps.board_summary(notes, by_id) == {
        "total": 3, "open": 2, "done": 1, "blocked": 1, "ready": 1,
    }
