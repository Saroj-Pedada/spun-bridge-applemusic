#!/usr/bin/env bash
# Stops spun-bridge and its managed Chrome instance. Spun is left running --
# stop it separately (or just close its window) if you want that gone too.
LOGDIR="$HOME/.cache/spun-bridge"
PIDFILE="$LOGDIR/shim.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")"
    echo "Stopped spun-bridge (pid $(cat "$PIDFILE"))."
    rm -f "$PIDFILE"
else
    echo "spun-bridge doesn't look like it's running."
fi

pkill -f "spun-bridge/chrome-profile" 2>/dev/null && echo "Cleaned up its Chrome process(es)."
