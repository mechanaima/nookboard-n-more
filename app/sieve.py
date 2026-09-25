"""What a sieve scrape *means*, with no sockets anywhere near it.

`sieve_run` opens the connections; this is the half that decides what a status
line, an error body, or a poll means. It is pure on purpose: every judgment the
integration makes -- is this run finished, is this turn the answer I asked for,
does this run's output count as clean data, is this failure worth retrying --
can be examined directly in a test, without a server and without a clock.

The contract being encoded, because getting any of it wrong costs credits or
silently shows the wrong answer:

- **Start is not idempotent and it spends credits.** There is no idempotency key
  and an accepted call creates a run. So a timeout or a network error on
  `POST /api/scrapes` is *ambiguous*, not a failure: the first call may have
  succeeded, and retrying could start (and bill for) a second run. That is
  `SieveStartAmbiguous`, and nothing here ever retries it.
- **Retrying after 429 or 5xx is safe** -- no run was created -- which is why
  `classify_error` marks exactly those (and transport errors on reads) retryable.
- **A run is minutes of work.** Poll at 5s and back off to ~30s; never a short
  timeout.
- **`done` is not the same as "the answer arrived".** A follow-up records a turn
  first, then polls until the run is `done` *and* the server's turn count has
  passed the one that was just added, or the previous answer is read as the new
  one.
- **`fail` schema conformance is never clean data.** "pass" is ok, "partial" is
  no violations but missing declared columns, "fail" is still non-conforming
  after repair, and "not_checkable"/"no_artifact" means there was nothing to
  check. `conformance_is_clean` is the one place that distinction lives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

#: Where the API lives. Overridable through `NOOKBOARD_SIEVE_BASE_URL`.
DEFAULT_BASE_URL = "https://scrape.usesieve.com"

#: How the site-access policy is set. "regular" unless the user explicitly picks
#: another one; "yolo" relaxes the site-access policy and is the user's call.
COMPLIANCE_MODES: tuple[str, ...] = ("conservative", "regular", "yolo")
DEFAULT_COMPLIANCE = "regular"

#: How the delivered table is shaped.
TABLE_SHAPES: tuple[str, ...] = ("long", "wide")

#: The strict output schema has a hard ceiling, in bytes of JSON.
OUTPUT_SCHEMA_MAX_BYTES = 32 * 1024

# -- polling -----------------------------------------------------------------

#: First poll, and the ceiling the backoff climbs to. A scrape is minutes of
#: work, so this is a heartbeat and not a spin: 5 -> 10 -> 20 -> 30 -> 30...
POLL_START = 5.0
POLL_MAX = 30.0
POLL_FACTOR = 2.0

# -- run statuses ------------------------------------------------------------

RUNNING = "running"
DONE = "done"
REFUSED = "refused"
KNOWN_RUN_STATUSES: frozenset[str] = frozenset({RUNNING, DONE, REFUSED})

# -- schema conformance ------------------------------------------------------

CONFORMANCE_PASS = "pass"
CONFORMANCE_PARTIAL = "partial"
CONFORMANCE_FAIL = "fail"
CONFORMANCE_NOT_CHECKABLE = "not_checkable"
CONFORMANCE_NO_ARTIFACT = "no_artifact"
KNOWN_CONFORMANCE: frozenset[str] = frozenset(
    {
        CONFORMANCE_PASS,
        CONFORMANCE_PARTIAL,
        CONFORMANCE_FAIL,
        CONFORMANCE_NOT_CHECKABLE,
        CONFORMANCE_NO_ARTIFACT,
    }
)

# -- device login ------------------------------------------------------------

DEVICE_PENDING = "authorization_pending"
DEVICE_SLOW_DOWN = "slow_down"
DEVICE_DENIED = "access_denied"
DEVICE_EXPIRED = "expired_token"


class SieveError(RuntimeError):
    """Anything sieve went wrong with. Callers catch this, not httpx."""


class SieveStartAmbiguous(SieveError):
    """`POST /api/scrapes` timed out or lost the network.

    The run may have started. It is never retried automatically: there is no
    idempotency key, and accepted calls spend credits. The caller has to tell
    the user to look at their sieve account rather than guess.
    """


@dataclass(frozen=True)
class SieveFailure:
    """One non-success answer, translated into what the app should do about it."""

    kind: str
    message: str
    retryable: bool
    status: int | None = None
    retry_after: float | None = None


class SieveHTTPError(SieveError):
    """A status the API returned, carrying the classified failure."""

    def __init__(self, failure: SieveFailure):
        super().__init__(failure.message)
        self.failure = failure


# -- request building --------------------------------------------------------


def _clean_urls(urls: Any) -> list[str]:
    out: list[str] = []
    for raw in urls or []:
        text = str(raw).strip()
        if text and text not in out:
            out.append(text)
    return out


def scrape_body(
    instruction: str,
    *,
    target_urls: Any = None,
    fields: Any = None,
    schema: Any = None,
    output_schema: Any = None,
    table_shape: str | None = None,
    compliance_mode: str | None = None,
) -> dict:
    """The JSON body for `POST /api/scrapes` (and for a follow-up turn).

    Only what is actually given is included, so a minimal "extract the text and
    author of each quote" call is a minimal request. `compliance_mode` defaults
    to "regular": the others are a deliberate choice the user makes.
    """
    body: dict[str, Any] = {
        "instruction": str(instruction).strip(),
        "compliance_mode": compliance_mode or DEFAULT_COMPLIANCE,
    }
    urls = _clean_urls(target_urls)
    if urls:
        body["target_urls"] = urls
    if fields:
        body["fields"] = [str(f) for f in fields]
    if schema is not None:
        body["schema"] = schema
    if output_schema is not None:
        body["output_schema"] = output_schema
    if table_shape:
        body["table_shape"] = table_shape
    return body


def validate_scrape_request(body: dict) -> list[str]:
    """Reasons this start body is not sendable. Empty means it is.

    Returned as a list of sentences so the API can say all of them at once
    instead of making the caller fix one, resubmit, and discover the next.
    """
    problems: list[str] = []
    if not str(body.get("instruction") or "").strip():
        problems.append("instruction is required (plain language, what to extract)")

    mode = body.get("compliance_mode") or DEFAULT_COMPLIANCE
    if mode not in COMPLIANCE_MODES:
        problems.append(f"compliance_mode must be one of {', '.join(COMPLIANCE_MODES)}")

    shape = body.get("table_shape")
    if shape is not None and shape not in TABLE_SHAPES:
        problems.append(f"table_shape must be one of {', '.join(TABLE_SHAPES)}")

    for url in body.get("target_urls") or []:
        parsed = urlparse(str(url))
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            problems.append(f"target_urls must be public http(s) pages, got {url!r}")

    output_schema = body.get("output_schema")
    if output_schema is not None:
        try:
            encoded = json.dumps(output_schema)
        except (TypeError, ValueError):
            problems.append("output_schema must be JSON-serialisable")
        else:
            if len(encoded.encode("utf-8")) > OUTPUT_SCHEMA_MAX_BYTES:
                problems.append(
                    f"output_schema is over {OUTPUT_SCHEMA_MAX_BYTES} bytes"
                )
    return problems


# -- polling / status --------------------------------------------------------


def next_poll_delay(previous: float | None) -> float:
    """5s, then 10, 20, 30, 30, ... -- the backoff after a poll that said running."""
    base = previous if previous and previous > 0 else POLL_START
    return min(max(base, POLL_START) * POLL_FACTOR, POLL_MAX)


def run_status(payload: Any) -> str:
    """The run's status, or a `SieveError` for anything that is not one of the three.

    "Treat any other value as an error" is the contract: a status the app does
    not understand must not be read as success or as still-running, because both
    guesses can show stale data as if it were the answer.
    """
    if not isinstance(payload, dict):
        raise SieveError("sieve returned something that is not a run")
    status = str(payload.get("status") or "").strip().lower()
    if status not in KNOWN_RUN_STATUSES:
        raise SieveError(
            f"unknown run status {payload.get('status')!r} "
            f"(expected one of {', '.join(sorted(KNOWN_RUN_STATUSES))})"
        )
    return status


def turns_of(payload: Any) -> int:
    """How many turns the server has recorded for this session."""
    if not isinstance(payload, dict):
        return 0
    try:
        return int(payload.get("turns") or 0)
    except (TypeError, ValueError):
        return 0


def turn_advanced(before: int, after: int) -> bool:
    """Has the server recorded a turn newer than `before`?"""
    return after > before


def ready_for_answer(payload: Any, awaiting_turn: int | None) -> bool:
    """Is the run done *and* has the turn we asked for landed?

    Without the second half, polling a follow-up reads the previous turn's
    answer: the run goes `done` the moment the message is accepted, before the
    new turn has produced anything.
    """
    if run_status(payload) != DONE:
        return False
    if awaiting_turn is None:
        return True
    return turns_of(payload) >= awaiting_turn


def refusal_of(payload: Any) -> dict:
    """The refusal record, with its `code`, on a refused run."""
    if not isinstance(payload, dict):
        return {}
    refusal = payload.get("refusal")
    return refusal if isinstance(refusal, dict) else {}


def refusal_code(payload: Any) -> str | None:
    code = refusal_of(payload).get("code")
    return str(code) if code else None


def files_of(payload: Any) -> list[dict]:
    """Delivered files, keeping only the ones that name themselves."""
    if not isinstance(payload, dict):
        return []
    out: list[dict] = []
    for entry in payload.get("files") or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not url:
            continue
        out.append(
            {
                "name": str(entry.get("name") or "download"),
                "size": entry.get("size"),
                "ext": entry.get("ext"),
                "url": str(url),
            }
        )
    return out


def conformance_of(payload: Any) -> dict | None:
    """The schema_conformance block, normalised, or None when there is none."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("schema_conformance")
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "").strip().lower()
    return {
        "status": status,
        "clean": status == CONFORMANCE_PASS,
        "reportable": status != CONFORMANCE_FAIL,
        "detail": raw,
        "note": conformance_note(status),
    }


def conformance_note(status: str) -> str:
    """A sentence for each conformance reading, because four are not "fine"."""
    return {
        CONFORMANCE_PASS: "the result matches the requested schema",
        CONFORMANCE_PARTIAL: (
            "no violations, but some declared columns are missing"
        ),
        CONFORMANCE_FAIL: (
            "the result is still non-conforming after repair - do not treat it as clean data"
        ),
        CONFORMANCE_NOT_CHECKABLE: "there was nothing the schema could be checked against",
        CONFORMANCE_NO_ARTIFACT: "the run produced no artifact to check",
    }.get(status, f"unrecognised schema conformance {status!r}")


def conformance_is_clean(payload: Any) -> bool:
    """Is this run's output a *pass*, the only reading that is clean data?

    "partial" has no violations but is not the declared shape, so it is not
    clean; "fail" is non-conforming and must never be presented as clean.
    """
    report = conformance_of(payload)
    return bool(report and report["status"] == CONFORMANCE_PASS)


def summarise_run(payload: Any) -> dict:
    """The pieces the API and the UI need, in one shape."""
    report = conformance_of(payload)
    return {
        "status": payload.get("status") if isinstance(payload, dict) else None,
        "turns": turns_of(payload),
        "summary": payload.get("summary") if isinstance(payload, dict) else None,
        "result": payload.get("result") if isinstance(payload, dict) else None,
        "files": files_of(payload),
        "schema_conformance": report,
        "refusal": refusal_of(payload) or None,
    }


# -- error mapping -----------------------------------------------------------


def _body_message(body: Any) -> str | None:
    if isinstance(body, dict):
        for key in ("error", "detail", "message"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = value.get("message") or value.get("error")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    if isinstance(body, str) and body.strip():
        return body.strip()[:300]
    return None


def classify_error(
    status: int, body: Any = None, *, retry_after: float | None = None
) -> SieveFailure:
    """Turn a non-success HTTP answer into what to do about it.

    400 fix the request, do not retry. 401 the key is missing or revoked. 402
    out of credits. 404 not found or not yours. 409 a turn is already in flight.
    429 wait `Retry-After`. 5xx and transport errors on reads are safe to retry;
    the ambiguous start is a separate exception entirely.
    """
    detail = _body_message(body)
    suffix = f" (sieve said: {detail})" if detail else ""
    if status == 400:
        return SieveFailure("bad_request", f"the request was rejected{suffix}", False, status)
    if status == 401:
        return SieveFailure("unauthorized", f"the sieve key is missing or revoked{suffix}", False, status)
    if status == 402:
        return SieveFailure(
            "out_of_credits",
            f"out of sieve credits - see GET /api/me/credits{suffix}",
            False,
            status,
        )
    if status == 404:
        return SieveFailure("not_found", f"that run does not exist or is not yours{suffix}", False, status)
    if status == 409:
        return SieveFailure(
            "conflict", f"a turn is already in flight for that run{suffix}", True, status
        )
    if status == 429:
        return SieveFailure(
            "rate_limited",
            f"sieve is rate-limiting; wait {('%.0f' % retry_after) + 's' if retry_after else 'a moment'}{suffix}",
            True,
            status,
            retry_after,
        )
    if status >= 500:
        return SieveFailure("server_error", f"sieve had a server error ({status}){suffix}", True, status)
    return SieveFailure(
        "http_error", f"sieve answered {status}{suffix}", status == 408, status
    )


def transport_failure(message: str, *, retryable: bool = True) -> SieveFailure:
    """A network-level failure, as a classified failure rather than an exception."""
    return SieveFailure("transport", message, retryable)


# -- files -------------------------------------------------------------------


def is_safe_file_url(url: Any, base_url: str) -> bool:
    """May the app fetch this file url?

    The API hands back *relative* urls, which the caller prefixes with the base
    and sends the Bearer header. An absolute url is allowed only when it is the
    same origin as the API: anything else would be the app being used as an
    open authenticated proxy for whatever the body happened to name.
    """
    text = str(url or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https"):
        base = urlparse(base_url)
        return (
            parsed.scheme == base.scheme
            and parsed.hostname == base.hostname
            and parsed.port == base.port
        )
    if parsed.scheme or parsed.netloc:
        return False
    return bool(parsed.path)


def absolute_file_url(url: Any, base_url: str) -> str:
    """Prefix a relative file url, leaving an already-absolute one alone."""
    text = str(url or "").strip()
    if urlparse(text).scheme:
        return text
    return urljoin(base_url.rstrip("/") + "/", text.lstrip("/"))


# -- device login ------------------------------------------------------------


def device_action(status: int, body: Any) -> str:
    """What a device-token poll response means.

    Returns "approved" | "pending" | "slow_down" | "denied" | "expired" |
    "error". The distinctions matter: "slow_down" means add 5s to the interval,
    "denied" means the user declined and the tool must stop, and "expired" means
    the ten-minute code is spent and the flow starts over at step one.
    """
    if status == 200:
        return "approved"
    if status == 400 and isinstance(body, dict):
        error = str(body.get("error") or "").strip()
        if error == DEVICE_PENDING:
            return "pending"
        if error == DEVICE_SLOW_DOWN:
            return "slow_down"
        if error == DEVICE_DENIED:
            return "denied"
        if error == DEVICE_EXPIRED:
            return "expired"
    return "error"
