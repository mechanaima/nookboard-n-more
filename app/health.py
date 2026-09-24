"""Are the things the vault points at answering? Asked, not assumed.

The Bookmarks README used to say this app does not probe your machines, and the
reason was sound: a health check is a second answer about state that goes stale
between looks, and a `GET` you did not ask for. That decision was reversed on
purpose, and this module is the price of reversing it honestly:

**A status is a fact with a timestamp, or it is a lie.** Every result here carries
`at`, the moment it was taken, and the view draws nothing as up without one. A view
that opens knowing nothing says *not checked* -- because it does.

Nothing in this file opens a socket. `interpret` turns a result somebody else
obtained into a kind and a sentence; the runner is `app/health_run.py`. Splitting
them is what lets the half with the judgments in it be tested without a network.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .bookmarks import url_problem

#: A local service answers in milliseconds. This is generous and -- the point --
#: bounded: a view that waits on a dead host must give up and say so.
CHECK_TIMEOUT = 2.5

#: It answered, and it liked the question.
UP = "up"
#: It answered, with a status that is not a success. Reachable is not the same as
#: working, and the number is the difference.
ANSWERED = "answered"
#: Nothing answered. The reason is the error, in words.
DOWN = "down"
#: Not asked -- a bookmark a browser cannot open anyway, or a check that has not run.
UNKNOWN = "unknown"

KINDS = (UP, ANSWERED, DOWN, UNKNOWN)


def is_checkable(url: str) -> bool:
    """Only an address a browser could open is worth asking about.

    An unusable one has no host to reach, and asking anyway would turn a typo into a
    second, unrelated complaint.
    """
    return url_problem(url) is None


def kind_of(status: int | None, error: str | None) -> str:
    """What a result means, from the only two things a result can carry.

    No status and no error is not "up": it is a check that did not happen.
    """
    if status is not None:
        return UP if 200 <= status < 400 else ANSWERED
    if error:
        return DOWN
    return UNKNOWN


def words(kind: str, status: int | None = None, seconds: float | None = None) -> str:
    """The chip: short, and never more certain than the check was.

    `up` is the only word that claims something works, and it is only used when a
    status said so. Everything else says what happened.
    """
    if kind == UP:
        return "answering"
    if kind == ANSWERED:
        return f"answered {status}" if status else "answered"
    if kind == DOWN:
        return "no answer"
    return "not checked"


def why(status: int | None, error: str | None) -> str | None:
    """One sentence of detail, or None. Shown beside the chip, not in place of it.

    A 4xx and a 5xx are not the same fact, which the first version of this got wrong
    by lumping them together as "the service is there and unhappy". A llama.cpp server
    answers the root path with 404 because it serves `/completion`, not `/` -- a
    service that is *working*, at an address that is not a page. Saying "unhappy"
    there is the app diagnosing something it did not look at.
    """
    if error:
        return error
    if status is None or 200 <= status < 400:
        return None
    if status < 500:
        return "it answered, but not with a page -- the address is there, that path is not"
    return "it answered, but with an error -- the service is there and unhappy"


def result(status: int | None = None, error: str | None = None,
           ms: float | None = None, at: str | None = None) -> dict:
    """One check, in the shape the view reads. Built here so it cannot drift."""
    kind = kind_of(status, error)
    return {
        "kind": kind,
        "status": status,
        "error": error,
        "ms": None if ms is None else round(ms),
        "at": at,
        "words": words(kind, status),
        "why": why(status, error),
    }


def now() -> str:
    """When a check happened, in UTC, so the browser can turn it into "4s ago"."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unanswered() -> dict:
    """The honest blank for an address nobody has asked about."""
    return result()
