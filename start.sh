#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
if [ -n "${FINANCE_PYTHON:-}" ]; then
    exec "$FINANCE_PYTHON" main.py "$@"
fi
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
if [ ! -f .venv/.finance-requirements ] || ! cmp -s requirements.txt .venv/.finance-requirements; then
    .venv/bin/python -m pip install -r requirements.txt
    cp requirements.txt .venv/.finance-requirements
fi
exec .venv/bin/python main.py "$@"
