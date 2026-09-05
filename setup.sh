#!/usr/bin/env bash
# One-shot setup. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "Creating virtualenv..."
  python3 -m venv venv
fi

echo "Installing dependencies..."
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt

echo
echo "Done. Start the app with:"
echo "    ./venv/bin/python app.py"
echo
echo "Then open http://127.0.0.1:5111 and add your Google API key under Settings."
echo "You'll need Places API (New) and Geocoding API enabled — see README.md."
