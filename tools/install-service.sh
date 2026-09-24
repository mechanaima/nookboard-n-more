#!/usr/bin/env bash
# Install (or remove) the user service that serves nookboard on 127.0.0.1:8765.
#
#   ./tools/install-service.sh            # install, enable at boot, start, report
#   ./tools/install-service.sh --remove   # stop it and delete the unit
#   ./tools/install-service.sh --dry-run  # show the unit, install nothing
#
# A *user* service, not a system one: the vault, the checkout and the interpreter
# all belong to one person. A system unit would need a User= to say so, root to
# change anything, and would run outside the session whose desktop the notifier
# talks to. `enable-linger` is what makes it start at boot rather than at login,
# and the script sets it.
#
# Idempotent on purpose: re-running rewrites the unit with the current checkout
# path and restarts it, so moving the repo is fixed by running this again rather
# than by hand-editing a unit file nobody will remember to look in.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="nookboard.service"
HOST=127.0.0.1
PORT=8765
MODE="install"

for arg in "$@"; do
  case "$arg" in
    --remove) MODE="remove" ;;
    --dry-run) MODE="dry" ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# The checkout's own interpreter: no uv on boot's critical path, nothing to
# resolve at 06:00 when nobody is watching.
if [[ -x "$REPO/.venv/bin/uvicorn" ]]; then
  EXEC="$REPO/.venv/bin/uvicorn"
elif command -v uv >/dev/null 2>&1; then
  EXEC="$(command -v uv) run --directory $REPO uvicorn"
else
  echo "no interpreter: $REPO/.venv/bin/uvicorn is missing and uv is not on PATH" >&2
  exit 1
fi

BODY="[Unit]
Description=nookboard - notes, tasks and a journal on $HOST:$PORT
Documentation=file://$REPO/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO
# No Environment=: 'make dev' sets none either, so this is the same app with the
# same defaults -- the vault in the checkout, Bonsai on :11440, the 22:00 recap.
ExecStart=$EXEC app.main:app --host $HOST --port $PORT
# Not --reload. This is the app, not a development server: a file watcher left
# running over the vault for the machine's whole uptime is a strange thing to
# own. Edit code, then 'systemctl --user restart nookboard'. To hack on it live,
# run it somewhere else: uv run uvicorn app.main:app --reload --port 8796
Restart=always
RestartSec=2
# Where a start that failed says why.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"

if [[ "$MODE" == "remove" ]]; then
  systemctl --user disable --now "$UNIT" 2>/dev/null || true
  rm -f "$UNIT_DIR/$UNIT"
  systemctl --user daemon-reload
  systemctl --user reset-failed "$UNIT" 2>/dev/null || true
  echo "removed $UNIT"
  exit 0
fi

if [[ "$MODE" == "dry" ]]; then
  echo "# $UNIT_DIR/$UNIT"; echo "$BODY"; exit 0
fi

# Something else holding the port is the one way this installs cleanly and then
# does nothing at all. Say so now, while the reason is still on screen, rather
# than leaving a unit that restarts every two seconds against a socket it cannot
# have. It is not this script's business to stop whatever that is.
if ! systemctl --user is-active --quiet "$UNIT"; then
  if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    echo "!! something is already listening on $PORT, so the service cannot take it."
    echo "   If that is your own 'make dev', stop it and run this again."
    echo "   (This script will not stop it for you.)"
    ss -ltnp 2>/dev/null | grep ":$PORT " | sed 's/^/     /'
    exit 1
  fi
fi

mkdir -p "$UNIT_DIR"
printf '%s' "$BODY" > "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"
sleep 1

echo "installed:"
echo "  unit:        $UNIT_DIR/$UNIT"
echo "  interpreter: $EXEC"
echo "  state:       $(systemctl --user is-active "$UNIT")"
echo
if curl -sf "http://$HOST:$PORT/api/health" >/dev/null; then
  echo "  answering:   http://$HOST:$PORT"
else
  echo "  NOT answering yet -- journalctl --user -u nookboard -n 30"
fi

# At boot rather than at login. Without this the service starts when you log in,
# which is not what 'on boot' means for a machine that is usually logged in.
if [[ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null)" != "yes" ]]; then
  if loginctl enable-linger "$USER" 2>/dev/null; then
    echo "  linger:      on (it will start at boot, before anyone logs in)"
  else
    echo "  linger:      could not set it -- run: loginctl enable-linger $USER"
  fi
else
  echo "  linger:      already on"
fi
echo
echo "  logs:        journalctl --user -u $UNIT -f"
echo "  restart:     systemctl --user restart nookboard"
echo "  remove:      $0 --remove"
