#!/usr/bin/env python3
"""Pop up a desktop notification when a note's time arrives.

Run it every minute; it decides what is due. `tools/nookboard-events.timer`
does exactly that, and this is what it runs.

The contract, in full, because a notification is an interruption and one that
lies is worse than none:

- **Only notes with a time.** A note with a date and no time belongs to a whole
  day, and a whole day is not a reason to interrupt anyone.
- **Once per occurrence.** The ledger of what has been announced lives beside
  your other app state (`~/.local/state/nookboard/notified.json`), keyed by note
  *and* instant, so editing a note's wording does not re-fire it. A re-run, a
  restart or a crash loop cannot make it fire twice.
- **Today only.** A machine that was off since Monday catches up on *today's*
  missed appointments and says how late they are; it does not deliver the week
  it slept through. Yesterday's appointment is not news, it is noise.
- **Says how late.** Being told at 14:31 that something started at 14:30 needs
  no comment. Being told at 16:00 that it started at 14:30 does, or the popup
  reads as if it were happening now.
- **These files are the app's.** The vault is read through `app.vault`, so
  frontmatter is parsed exactly the way the app parses it. If this script ever
  disagreed with the app about what a note says, there would be two answers to
  one question.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app import schedule  # noqa: E402
from app.vault import Vault  # noqa: E402

#: How long a fired record is kept. Long enough that a re-run after a holiday
#: cannot re-fire, short enough that the file stays a small list.
LEDGER_RETENTION = timedelta(days=30)


def default_state_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "nookboard" / "notified.json"


def load_ledger(path: Path) -> set[str]:
    """What has already been announced. An unreadable ledger is an empty one:
    the cost of a missing file is at worst one repeated popup, while refusing to
    run would mean silence from a notifier that looks installed."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    if isinstance(raw, dict):
        raw = raw.get("fired", [])
    return {str(k) for k in raw} if isinstance(raw, list) else set()


def save_ledger(path: Path, keys: set[str], now: datetime) -> None:
    """Keep only keys recent enough to matter, so the file cannot grow forever.

    The timestamp is the occurrence's own, taken from the key: pruning by when
    we wrote the file would drop things that fired late.
    """
    kept = sorted(k for k in keys if _key_is_recent(k, now))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fired": kept}, indent=2) + "\n")


def _key_is_recent(key: str, now: datetime) -> bool:
    stamp = key.rsplit("@", 1)[-1]
    try:
        return datetime.fromisoformat(stamp) >= now - LEDGER_RETENTION
    except ValueError:
        return False


def notifier_command() -> list[str] | None:
    """The popup tool, or `None` if this machine has none.

    `NOOKBOARD_NOTIFY_CMD` overrides both, which is how the tests watch what
    would have been sent without a desktop being involved.
    """
    override = os.environ.get("NOOKBOARD_NOTIFY_CMD")
    if override:
        return override.split()
    for tool in ("dunstify", "notify-send"):
        found = shutil.which(tool)
        if found:
            # `-a` so the popup is attributable in the daemon's history; the
            # rest is urgency + summary + body, which every daemon understands.
            return [found, "-a", "nookboard", "-u", "normal"]
    return None


def message_for(occurrence: schedule.Occurrence, now: datetime) -> tuple[str, str]:
    """What the popup says: the note's own title, and when it is."""
    note = occurrence.note
    parts = [schedule.label(note) or schedule.clock_text(occurrence.start)]
    late = schedule.how_late(occurrence.start, now)
    if late:
        parts.append(f"({late})")
    if note.collection and note.collection != "inbox":
        parts.append(f"\u00b7 {note.collection}")
    return note.title, " ".join(parts)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", default=os.environ.get("NOOKBOARD_VAULT", str(REPO / "vault")))
    parser.add_argument("--state", default=str(default_state_path()))
    parser.add_argument("--now", help="pretend it is this time (ISO 8601), for testing")
    parser.add_argument("--dry-run", action="store_true", help="say what would fire, send nothing")
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now()
    vault_path = Path(args.vault).expanduser()
    if not vault_path.is_dir():
        print(f"no vault at {vault_path}", file=sys.stderr)
        return 2

    state_path = Path(args.state).expanduser()
    fired = load_ledger(state_path)
    # Local midnight: today's missed appointments, not last week's.
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)

    pending = schedule.due(Vault(vault_path).list_all(), now, fired=fired, since=since)

    command = notifier_command()
    if pending and command is None and not args.dry_run:
        print(
            "nothing to send with: neither dunstify nor notify-send is installed",
            file=sys.stderr,
        )
        return 3

    for occurrence in pending:
        title, body = message_for(occurrence, now)
        if args.dry_run:
            print(f"would notify: {title} -- {body}")
            continue
        done = subprocess.run([*command, title, body], capture_output=True, text=True)
        if done.returncode != 0:
            # Left unrecorded on purpose: a popup that failed to appear must not
            # be marked as announced. The next tick tries again.
            print(f"notify failed ({done.returncode}): {done.stderr.strip()}", file=sys.stderr)
            return 4
        fired.add(occurrence.key)
        print(f"notified: {title} -- {body}")

    if pending and not args.dry_run:
        save_ledger(state_path, fired, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
