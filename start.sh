#!/bin/bash
# Start script for Railway deployment
# 1. Run the data fetcher immediately
# 2. Start a background loop to refresh data every hour
# 3. Start the web server in foreground

echo "=== Initial data fetch ==="
python3 fetcher/main.py

echo "=== Starting 10-minute refresh loop ==="
while true; do
  sleep 600
  echo "=== 10-minute refresh ==="
  python3 fetcher/main.py
done &

echo "=== Starting Kalshi 1-minute refresh loop ==="
# Independent of the main fetcher. The Kalshi fetcher uses parallel
# per-series queries (~3s typical) so it comfortably fits a 1-min loop.
# If a fetch fails (rate-limit, transient network), we swallow the error
# and try again in 1 minute — main dashboard data is unaffected.
(
  python3 fetcher/sources/kalshi.py || true
  while true; do
    sleep 60
    python3 fetcher/sources/kalshi.py || true
  done
) &

echo "=== Starting web server ==="
python3 server.py
