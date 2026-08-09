#!/bin/sh
# Start the API, applying database migrations first.
#
# This exists instead of putting `alembic upgrade head && python run.py` in a
# host's start-command field. The `&&` is a shell operator, and some hosts
# (Render among them) pass that field to exec without a shell -- so the whole
# string is looked up as one program name and fails with "not found" (exit
# 127). Keeping the sequence inside a script means the host only ever runs a
# single, operator-free command: `sh scripts/start.sh`.
#
# alembic is idempotent, so running it on every start is safe and keeps the
# schema current without a separate step. exec replaces this shell with the
# app process so signals (Render's shutdown, SIGTERM) reach it directly.
set -e
alembic upgrade head
exec python run.py
