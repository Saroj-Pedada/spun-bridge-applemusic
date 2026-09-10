#!/usr/bin/env bash
# Starts spun-bridge (Chrome + MPRIS + local API), then launches Spun.
set -e
cd "$(dirname "$0")"

LOGDIR="$HOME/.cache/spun-bridge"
mkdir -p "$LOGDIR"
PIDFILE="$LOGDIR/shim.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "spun-bridge is already running (pid $(cat "$PIDFILE"))."
else
    echo "Starting spun-bridge..."
    .venv/bin/python -m spun_bridge.main > "$LOGDIR/shim.log" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 4
    if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "spun-bridge failed to start -- check $LOGDIR/shim.log"
        exit 1
    fi
    echo "spun-bridge running (pid $(cat "$PIDFILE")); log: $LOGDIR/shim.log"
fi

echo "A Chrome window should appear (or already be open) on Apple Music --"
echo "it should still be signed in from last time. Play something there,"
echo "or just leave it and control playback from Spun once it connects."
echo

SPUN_BIN="$HOME/Spun/build/spun"
if [ -x "$SPUN_BIN" ]; then
    echo "Launching Spun..."
    "$SPUN_BIN" &
    disown
    echo "Done. Spun should pick up what's playing automatically."
else
    echo "Spun binary not found at $SPUN_BIN -- spun-bridge is running on its"
    echo "own; build/install Spun (see README) then launch it separately."
fi
