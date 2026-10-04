#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec .venv-local/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log --timeout-graceful-shutdown 3
