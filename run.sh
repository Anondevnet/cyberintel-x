#!/usr/bin/env bash
# Creates a project-local virtualenv (never touches global Python), installs deps, starts the app.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
"$PY" -c 'import sqlite3; c=sqlite3.connect(":memory:"); c.execute("create virtual table t using fts5(x)")' \
  || { echo "Your Python's SQLite lacks FTS5. Use a python.org / Homebrew / pyenv build."; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example (edit it to add tokens)."; }
exec .venv/bin/python -m cyberintel "${@:-serve}"
