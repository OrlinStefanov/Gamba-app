#!/usr/bin/env bash
# macOS / Linux launcher for the beach flag predictor.
# No virtualenv and no install step: the app is standard library only.
set -euo pipefail

cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  exec python3 -m beachflag "$@"
fi

echo "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/" >&2
exit 1
