"""Task dependency graph: blockers, blocked-ness, cycle prevention, board order.

Dependencies are stored in **one direction only** — `blocked_by` on the note
that waits. "What does this block?" is the reverse edge and is always derived
here. Two stored directions would be two things that can disagree; one stored
direction cannot drift.

A task is **blocked** when any note it waits on is not closed. A blocker that
no longer exists also counts as blocking, and is reported as `missing`: silently
treating a dangling reference as satisfied would make work look ready when the
gate it was waiting on has been deleted.

Everything in this module is pure — Notes in, values out, no I/O — so the API,
the index and the tests all exercise the same rules.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Optional, Sequence

from .models import Note, Stage, Status, stage_for_status

#: A task whose status is one of these is finished, so it cannot block anything.
CLOSED: frozenset[str] = frozenset({Status.COMPLETE.value, Status.IRRELEVANT.value})

#: Board columns, left to right.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.BACKLOG, Stage.TODO, Stage.DOING, Stage.REVIEW, Stage.DONE,
)


def is_closed(note: Note) -> bool:
    return note.status.value in CLOSED


def stage_of(note: Note) -> str:
    """This note's column, derived when nobody has placed it by hand."""
    return note.stage or stage_for_status(note.status).value


def index_by_id(notes: Iterable[Note]) -> dict[str, Note]:
    return {n.id: n for n in notes}


# -- the graph ---------------------------------------------------------------


def resolve(ids: Sequence[str], by_id: Mapping[str, Note]) -> list[dict]:
    """Turn stored blocker ids into display records.

    `missing` is carried explicitly rather than dropped, so the UI can say
    "blocked by something that no longer exists" instead of showing nothing.
    """
    out: list[dict] = []
    for dep_id in ids:
        dep = by_id.get(dep_id)
        if dep is None:
            out.append({
                "id": dep_id,
                "title": dep_id,
                "status": "missing",
                "stage": None,
                "closed": False,
                "missing": True,
            })
            continue
        out.append({
            "id": dep.id,
            "title": dep.title,
            "status": dep.status.value,
            "stage": stage_of(dep),
            "closed": is_closed(dep),
            "missing": False,
        })
    return out


def open_blockers(note: Note, by_id: Mapping[str, Note]) -> list[dict]:
    """The subset of this note's blockers that are still holding it up."""
    return [b for b in resolve(note.blocked_by, by_id) if not b["closed"]]


def is_blocked(note: Note, by_id: Mapping[str, Note]) -> bool:
    return bool(open_blockers(note, by_id))


def blocking(note_id: str, notes: Iterable[Note]) -> list[Note]:
    """Notes that wait on `note_id` — the derived reverse edge."""
    return [n for n in notes if note_id in n.blocked_by and n.id != note_id]


def normalize_blocked_by(
    ids: Iterable[str], *, note_id: str, by_id: Mapping[str, Note]
) -> list[str]:
    """Clean a proposed blocker list: no self-reference, no duplicates.

    Unknown ids are kept — a dependency on a note that is not indexed yet is
    still a real dependency, and it will resolve once the note exists.
    """
    out: list[str] = []
    for raw in ids:
        dep = str(raw).strip()
        if not dep or dep == note_id or dep in out:
            continue
        out.append(dep)
    return out


def find_cycle(note_id: str, new_blocker_id: str, by_id: Mapping[str, Note]) -> list[str] | None:
    """Path that adding `note_id` waits-on `new_blocker_id` would close.

    Returns the offending chain (for a human-readable error), or None if the
    edge is safe. A cycle here is not a cosmetic problem: A waits on B waits on
    A means neither can ever legitimately start, and every naive graph walk
    (including reversing a blocker chain) would recurse forever.
    """
    if note_id == new_blocker_id:
        return [note_id, note_id]

    # Walk down from the proposed blocker: if we can get back to note_id, the
    # new edge closes a loop.
    stack: list[tuple[str, list[str]]] = [(new_blocker_id, [note_id, new_blocker_id])]
    seen: set[str] = set()
    while stack:
        current, path = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        node = by_id.get(current)
        if node is None:
            continue
        for nxt in node.blocked_by:
            if nxt == note_id:
                return path + [note_id]
            if nxt not in seen:
                stack.append((nxt, path + [nxt]))
    return None


def check_blockers(
    note_id: str, proposed: Sequence[str], by_id: Mapping[str, Note]
) -> list[str] | None:
    """First cycle found in a proposed blocker list, or None."""
    for dep in proposed:
        cycle = find_cycle(note_id, dep, by_id)
        if cycle is not None:
            return cycle
    return None


def bottleneck_counts(notes: Iterable[Note]) -> dict[str, int]:
    """How many open tasks each note is holding up."""
    counts: dict[str, int] = {}
    for n in notes:
        for dep in n.blocked_by:
            if dep == n.id:
                continue
            counts[dep] = counts.get(dep, 0) + 1
    return counts


def reconcile_move(stage: str, status: Status) -> tuple[str, Status]:
    """Status after a card is dropped into `stage`.

    A drag states its intent in terms of the column, so the column wins: landing
    in Done completes the task, and leaving Done reopens it. Without that second
    half a card dragged out of Done would sit in Doing while still claiming to be
    complete — exactly the contradiction this module exists to prevent.
    """
    if stage == Stage.DONE.value:
        # A card that was dropped stays dropped; landing in Done must not
        # relabel "I decided not to do this" as "I did this".
        if status is Status.IRRELEVANT:
            return stage, status
        return stage, Status.COMPLETE
    if status in (Status.COMPLETE, Status.IRRELEVANT):
        return stage, Status.OPEN
    return stage, status


def next_position(notes: Iterable[Note], stage: str) -> float:
    """A slot at the bottom of `stage` for a card that is just being created.

    Cards are ordered by an explicit `position`, but `created` is only a *date*,
    so every task captured in one sitting ties on creation and would otherwise
    fall back to id (i.e. alphabetical). Assigning the slot up front is what
    makes a task board show tasks in the order you thought of them.
    """
    taken = [n.position for n in notes if stage_of(n) == stage and n.position is not None]
    return max(taken) + 1.0 if taken else 1.0


# -- board order -------------------------------------------------------------


def order_key(note: Note) -> tuple:
    """Canonical in-column sort key.

    Pinned notes float to the top. Explicit positions win and are compared as
    floats; notes that were never placed fall to the bottom in creation order.
    The `is None` flag leads the tuple so Python never has to compare None with
    a float.
    """
    return (
        not note.pinned,  # pinned first (False < True)
        note.position is None,
        note.position if note.position is not None else 0.0,
        note.created.toordinal(),
        note.id,
    )


def sort_column(notes: Iterable[Note]) -> list[Note]:
    return sorted(notes, key=order_key)


def plan_move(
    cards_in_target: Sequence[Note], moving: Note, before_id: Optional[str]
) -> dict[str, float]:
    """Positions to write when `moving` lands in a column.

    `cards_in_target` is the target column **excluding** `moving`, already in
    display order. Positions are rewritten as 1..N rather than averaged between
    neighbours: midpoint insertion drifts into ever-longer floats after enough
    drags, and integers stay readable in the Markdown. Only cards whose position
    actually changes are returned, so a normal drag rewrites one or two files.
    """
    order: list[Note] = list(cards_in_target)
    at = len(order)
    if before_id is not None:
        for i, card in enumerate(order):
            if card.id == before_id:
                at = i
                break
    order.insert(at, moving)

    out: dict[str, float] = {}
    for i, card in enumerate(order):
        wanted = float(i + 1)
        if card.id == moving.id or card.position != wanted:
            out[card.id] = wanted
    return out


def _is_due_soon(date_val, today):
    """True when date_val falls in the window [today, today+2 days]."""
    if not date_val:
        return False
    diff = (date_val - today).days
    return 0 <= diff <= 2


def board_summary(notes: Sequence[Note], by_id: Mapping[str, Note]) -> dict:
    """Counts for the board header — the numbers that make progress legible."""
    open_notes = [n for n in notes if not is_closed(n)]
    blocked = [n for n in open_notes if is_blocked(n, by_id)]
    today = date.today()
    due_soon = sum(
        1 for n in notes
        if n.dates and _is_due_soon(n.dates[0], today)
    )
    return {
        "total": len(notes),
        "open": len(open_notes),
        "done": len(notes) - len(open_notes),
        "blocked": len(blocked),
        "ready": len(open_notes) - len(blocked),
        "due_soon": due_soon,
    }
