"""The half of the health check that opens sockets.

One request per address, all of them at once, each bounded by `CHECK_TIMEOUT`, and
every one of them *closed as soon as the status line arrives* -- the app wants to
know whether a service answered, not to download it. Nothing here reads a body, so
a bookmark to a large page costs the same as a bookmark to a small one.

This is the only place in nookboard that makes an outbound request. It is also, for
that reason, the only place where the app can hang on something outside the machine:
hence the timeout, the thread pool, and the rule that a failed check is a *result*
-- never an exception that climbs into the view.
"""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import health
from .workspace import relative_age

#: Enough for a handful of bookmarks without waiting in a queue behind them.
MAX_PARALLEL = 8

#: A user agent that says what it is. The app is the only caller, and a service
#: owner reading their logs deserves to know which thing on this machine asked.
AGENT = "nookboard-bookmarks/1.0"


def _reason(err: BaseException, timeout: float = health.CHECK_TIMEOUT) -> str:
    """A failure as a sentence, in the words a person would use.

    `ConnectionRefusedError` is precise and useless; "nothing is listening on that
    port" is what someone with a service they forgot to start needs to read.

    `timeout` is the one that was actually used, not the default: a sentence that
    quotes 2.5 seconds after waiting 0.3 is the app stating a number it did not
    measure, which is the whole thing this feature is not allowed to do.
    """
    if isinstance(err, urllib.error.HTTPError):
        return f"it answered {err.code}"
    if isinstance(err, urllib.error.URLError):
        inner = err.reason
        if isinstance(inner, ConnectionRefusedError):
            return "nothing is listening on that port"
        if isinstance(inner, (socket.timeout, TimeoutError)):
            return f"no answer within {timeout:g} seconds"
        if isinstance(inner, socket.gaierror):
            return "that name does not resolve"
        if isinstance(inner, ssl.SSLError):
            return "the secure connection could not be established"
        return str(inner)
    if isinstance(err, (socket.timeout, TimeoutError)):
        return f"no answer within {timeout:g} seconds"
    return str(err)


def probe(url: str, timeout: float = health.CHECK_TIMEOUT,
          opener: urllib.request.OpenerDirector | None = None) -> dict:
    """Ask one address whether it is there, and come back with a result either way.

    `opener` exists so the tests can answer without a network, and so the one caller
    that does not want redirects followed can say so.
    """
    send = (opener or urllib.request).urlopen
    request = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": AGENT,
                                              "Accept": "*/*"})
    started = time.monotonic()
    status: int | None = None
    error: str | None = None
    try:
        with send(request, timeout=timeout) as resp:
            status = resp.status
            # Deliberately no read: the status line is the whole question, and a
            # bookmark to a streaming endpoint must not become a download.
    except urllib.error.HTTPError as err:      # an answer, just not a happy one
        status = err.code
        err.close()
    except Exception as err:                   # noqa: BLE001 -- any failure is a result
        error = _reason(err, timeout)
    return health.result(status=status, error=error,
                         ms=(time.monotonic() - started) * 1000,
                         at=health.now())


def check_all(urls: list[str], timeout: float = health.CHECK_TIMEOUT,
              opener: urllib.request.OpenerDirector | None = None) -> dict:
    """Every checkable address, at once, keyed the way the view looks them up.

    An address a browser could not open is reported as `unknown` rather than probed:
    it has no host to reach, and "no answer" for a typo would be a second, unrelated
    complaint about the same mistake.
    """
    wanted = [u for u in dict.fromkeys(urls) if health.is_checkable(u)]
    skipped = [u for u in dict.fromkeys(urls) if not health.is_checkable(u)]

    results: dict[str, dict] = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(wanted))) as pool:
            for url, res in zip(wanted, pool.map(lambda u: probe(u, timeout, opener), wanted)):
                results[url] = res
    for url in skipped:
        results[url] = health.unanswered()

    counts = {kind: 0 for kind in health.KINDS}
    for res in results.values():
        counts[res["kind"]] += 1

    when = health.now()
    return {
        "checked": when,
        # The age in words, computed once, here -- the same `relative_age` the
        # workspace cards use, so "a month" means one thing in this app. The browser
        # renders the string; it does not do the arithmetic.
        "checked_words": relative_age(when, datetime.now(timezone.utc)),
        "results": results,
        "counts": counts,
    }
