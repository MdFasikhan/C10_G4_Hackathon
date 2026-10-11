#!/usr/bin/env bash
# One-time setup for macOS/Linux. Then start the two services shown at the end.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env - add your OPENROUTER_API_KEY"; }
echo "Terminal 1: .venv/bin/uvicorn api.main:app --reload --port 8000"
echo "Terminal 2: .venv/bin/streamlit run app/streamlit_app.py"
