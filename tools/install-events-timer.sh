#!/usr/bin/env bash
# Install (or remove) the user timer that fires nookboard's timed notes.
#
#   ./tools/install-events-timer.sh            # install, start, and report
#   ./tools/install-events-timer.sh --remove   # stop and delete the units
#   ./tools/install-events-timer.sh --dry-run  # show the units, install nothing
#
# Idempotent on purpose: re-running rewrites the unit files with the current
# checkout path and restarts the timer, so moving the repo is fixed by running
# this again rather than by hand-editing a unit file that nobody will remember
# to look in.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE="nookboard-events.service"
TIMER="nookboard-events.timer"
MODE="install"

for arg in "$@"; do
  case "$arg" in
    --remove) MODE="remove" ;;
    --dry-run) MODE="dry" ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Prefer the checkout's own interpreter: no uv on the timer's critical path, no
# environment resolution to go wrong at 14:30 when nobody is watching.
if [[ -x "$REPO/.venv/bin/python" ]]; then
  PY="$REPO/.venv/bin/python"
  exec_line="$PY $REPO/tools/notify-events.py"
elif command -v uv >/dev/null 2>&1; then
  exec_line="$(command -v uv) run --directory $REPO python $REPO/tools/notify-events.py"
else
  echo "no interpreter: $REPO/.venv/bin/python is missing and uv is not on PATH" >&2
  exit 1
fi

if [[ "$MODE" == "remove" ]]; then
  systemctl --user disable --now "$TIMER" 2>/dev/null || true
  rm -f "$UNIT_DIR/$TIMER" "$UNIT_DIR/$SERVICE"
  systemctl --user daemon-reload
  systemctl --user reset-failed "$SERVICE" 2>/dev/null || true
  echo "removed $TIMER and $SERVICE"
  exit 0
fi

SERVICE_BODY="[Unit]
Description=Say when a nookboard note's time has arrived

[Service]
Type=oneshot
WorkingDirectory=$REPO
ExecStart=$exec_line
# A tick that cannot reach the desktop is still reported, in the journal.
StandardError=journal
"

TIMER_BODY="[Timer]
OnCalendar=*:*:00
# Catch a tick missed while the machine was asleep: the notifier reaches back
# only to midnight, so a late tick is useful where a stale one would be noise.
Persistent=true
AccuracySec=5s
RandomizedDelaySec=0

[Install]
WantedBy=timers.target
"

if [[ "$MODE" == "dry" ]]; then
  echo "# $UNIT_DIR/$SERVICE"; echo "$SERVICE_BODY"
  echo "# $UNIT_DIR/$TIMER"; echo "$TIMER_BODY"
  exit 0
fi

mkdir -p "$UNIT_DIR"
printf '%s' "$SERVICE_BODY" > "$UNIT_DIR/$SERVICE"
printf '%s' "$TIMER_BODY" > "$UNIT_DIR/$TIMER"
systemctl --user daemon-reload
systemctl --user enable --now "$TIMER"

echo "installed:"
echo "  interpreter: $exec_line"
systemctl --user list-timers "$TIMER" --no-pager | sed 's/^/  /'
echo
echo "next minute it will fire any note whose time has arrived."
echo "  watch it work:  journalctl --user -u $SERVICE -f"
echo "  turn it off:    $0 --remove"
