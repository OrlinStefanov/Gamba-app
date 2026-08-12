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

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY is not set."
  echo "Get a key at https://platform.openai.com/api-keys, then:"
  echo "    export OPENAI_API_KEY=sk-proj-..."
  echo
fi

exec .venv/bin/python -m gamba "$@"
