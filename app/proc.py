"""Run one command, and never let it become an exception.

Two layers in this app shell out — the workspace reader and the vault's history —
and both want the same three things from a subprocess: a timeout, text back, and a
failure that arrives as a *sentence* rather than a traceback. A folder git cannot
read is a fact about the folder; it is not a reason for a page to stop rendering.

Nothing here knows what it is running. That is the point: this is the piece both
callers can share instead of each growing their own copy of the same four
`except` clauses.
"""

from __future__ import annotations

import subprocess

#: git and grep answer in milliseconds. A wedged one — a network filesystem, a
#: stale index.lock — must not hold a request open, so there is a ceiling and it
#: is written down rather than inherited from a default nobody chose.
GIT_TIMEOUT = 15


def run(argv: list[str], timeout: int = GIT_TIMEOUT) -> tuple[str, str, int]:
    """Run a command. Returns `(stdout, stderr, code)`.

    A missing binary, a timeout and an OS-level refusal all come back as the
    third element of a tuple with a sentence in the second — never as an
    exception, and never as a silent success: exit code 127 means "not
    installed", 124 means "did not answer", and both are things a caller is
    expected to say out loud.
    """
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        return "", f"{argv[0]} is not installed", 127
    except subprocess.TimeoutExpired:
        return "", f"{argv[0]} did not answer within {timeout}s", 124
    except OSError as error:  # a bad path, a permission problem
        return "", str(error), 126
    return done.stdout, done.stderr.strip(), done.returncode
