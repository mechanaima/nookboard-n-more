#!/usr/bin/env python3
"""Get a sieve api key into nookboard's secret store, once, with you present.

This is the device-code flow, driven from your terminal. It prints a link and a
short code; **you** open the link, sign in, check the code matches, and click
Approve. This tool never opens the link, never signs in, and never approves on
your behalf -- that is the whole point of the flow, and it is also how the flow
is attacked: in 2026 someone starts a device login themselves and talks you into
approving *their* code. So approve only a code you started, in a terminal you
ran, and check that the page's code matches the one below.

On approval the key is written straight into the repository's `.env` (0600,
gitignored) as `SIEVE_API_KEY` and is never printed, logged, or echoed.

    ./tools/sieve-login.py
    ./tools/sieve-login.py --base-url https://scrape.usesieve.com

A key you already have can go into Settings -> API keys instead; paste it into
`.env` by hand and skip this tool.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app import sieve  # noqa: E402
from app.config import ENV_FILE, write_env_secret  # noqa: E402

#: What the approval page says the tool is. It is self-reported by the agent, so
#: the page labels it as such; keep it honest anyway.
CLIENT_NAME = "nookboard"


def _post(base: str, path: str, payload: dict, timeout: float = 20.0) -> tuple[int, object]:
    """POST JSON and return `(status, body)`. Errors come back, they do not raise."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, (json.loads(raw) if raw else {})
        except ValueError:
            return err.code, raw.decode(errors="replace")
    except urllib.error.URLError as err:
        print(f"could not reach {base}: {err.reason}", file=sys.stderr)
        raise SystemExit(1) from err


def login(base: str, client_name: str, env_file: Path) -> int:
    status, code = _post(base, "/api/auth/device/code", {"client_name": client_name})
    if status != 200 or not isinstance(code, dict) or not code.get("device_code"):
        print(f"sieve refused the device login ({status}): {code}", file=sys.stderr)
        return 1

    link = code.get("verification_uri_complete") or code.get("verification_uri")
    user_code = code.get("user_code") or ""
    print()
    print("Open this link in your browser and approve the request:")
    print(f"    {link}")
    print()
    print(f"The page shows a code; check it matches: {user_code}")
    print("Approve only a code you started yourself. Anyone who asks you to")
    print("approve theirs is trying to take your account, not help you.")
    print()

    interval = float(code.get("interval") or 5)
    expires_in = float(code.get("expires_in") or 600)
    device_code = str(code["device_code"])
    deadline = time.monotonic() + expires_in

    while time.monotonic() < deadline:
        time.sleep(interval)
        status, body = _post(base, "/api/auth/device/token", {"device_code": device_code})
        action = sieve.device_action(status, body)

        if action == "pending":
            continue
        if action == "slow_down":
            # The API is asking for more room; the spec says five seconds more.
            interval += 5
            continue
        if action == "denied":
            print("The request was declined. Nothing was written.", file=sys.stderr)
            return 2
        if action == "expired":
            print("The code expired before it was approved. Run the tool again.", file=sys.stderr)
            return 3
        if action == "approved":
            key = body.get("api_key") if isinstance(body, dict) else None
            if not key:
                print("sieve approved the login but returned no key.", file=sys.stderr)
                return 1
            target = write_env_secret("SIEVE_API_KEY", str(key), path=env_file)
            name = body.get("key_name") if isinstance(body, dict) else None
            # Deliberately never print `key`: it has full account access.
            print(f"Approved{' as ' + str(name) if name else ''}.")
            print(f"SIEVE_API_KEY written to {target} (not shown).")
            print("Restart nookboard (or `make dev`) to pick it up.")
            return 0

        print(f"unexpected answer from sieve ({status}): {body}", file=sys.stderr)
        return 1

    print("The code expired before it was approved. Run the tool again.", file=sys.stderr)
    return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default=os.environ.get("NOOKBOARD_SIEVE_BASE_URL", sieve.DEFAULT_BASE_URL),
        help="sieve API base (default %(default)s)",
    )
    parser.add_argument("--client-name", default=CLIENT_NAME, help="the tool name sieve shows")
    parser.add_argument(
        "--env-file",
        default=str(ENV_FILE),
        help="where to write SIEVE_API_KEY (default %(default)s)",
    )
    args = parser.parse_args(argv)
    return login(args.base_url.rstrip("/"), args.client_name, Path(args.env_file))


if __name__ == "__main__":
    raise SystemExit(main())
