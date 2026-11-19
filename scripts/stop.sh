#!/usr/bin/env bash
# Graceful stop (SIGTERM): running jobs get up to ~25 s to finish; anything interrupted is recovered on start.
cd "$(dirname "$0")/.."
[ -f storage/autocontent.pid ] || { echo "Not running"; exit 0; }
kill -TERM "$(cat storage/autocontent.pid)" 2>/dev/null && echo "Stopping…"
rm -f storage/autocontent.pid
