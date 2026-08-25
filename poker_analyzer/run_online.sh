#!/usr/bin/env bash
# Start Poker Analyzer in online (cloud) mode.
set -euo pipefail
cd "$(dirname "$0")"

export POKER_MODE=online
export POKER_HOST="${POKER_HOST:-127.0.0.1}"
export POKER_PORT="${POKER_PORT:-8000}"
# Permanent uploads live outside the git repo (per-user cookie id under users/).
export POKER_DATA_ROOT="${POKER_DATA_ROOT:-$HOME/poker_data}"
export POKER_IDLE_TTL_SEC="${POKER_IDLE_TTL_SEC:-1800}"
export POKER_MAX_CACHED_USERS="${POKER_MAX_CACHED_USERS:-2}"
export POKER_MAX_HANDS="${POKER_MAX_HANDS:-250000}"

exec python3 app.py
