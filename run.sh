#!/usr/bin/env bash
# macOS / Linux launcher. First run creates .venv and installs dependencies.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY is not set."
  echo "Get a key at https://console.anthropic.com/settings/keys, then:"
  echo "    export ANTHROPIC_API_KEY=sk-ant-..."
  echo
fi

exec .venv/bin/python -m gamba "$@"
