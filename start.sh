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

echo "=== Starting Kalshi 2-minute refresh loop ==="
# Independent of the main fetcher. If a Kalshi fetch fails (rate-limit,
# transient network), we ignore the error and try again in 2 minutes —
# main dashboard data is unaffected.
(
  python3 fetcher/sources/kalshi.py || true
  while true; do
    sleep 120
    python3 fetcher/sources/kalshi.py || true
  done
) &

echo "=== Starting web server ==="
python3 server.py
