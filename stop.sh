#!/usr/bin/env bash
# Stops cider-shim and its managed Chrome instance. Spun is left running --
# stop it separately (or just close its window) if you want that gone too.
LOGDIR="$HOME/.cache/cider-shim"
PIDFILE="$LOGDIR/shim.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")"
    echo "Stopped cider-shim (pid $(cat "$PIDFILE"))."
    rm -f "$PIDFILE"
else
    echo "cider-shim doesn't look like it's running."
fi

pkill -f "cider-shim/chrome-profile" 2>/dev/null && echo "Cleaned up its Chrome process(es)."
