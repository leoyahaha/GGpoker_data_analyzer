#!/usr/bin/env bash
# Start Poker Analyzer in online (cloud) mode.
set -euo pipefail
cd "$(dirname "$0")"

export POKER_MODE=online
export POKER_HOST="${POKER_HOST:-0.0.0.0}"
export POKER_PORT="${POKER_PORT:-8000}"
export POKER_DATA_ROOT="${POKER_DATA_ROOT:-./online_data}"
export POKER_IDLE_TTL_SEC="${POKER_IDLE_TTL_SEC:-1800}"
export POKER_MAX_CACHED_USERS="${POKER_MAX_CACHED_USERS:-2}"
export POKER_MAX_HANDS="${POKER_MAX_HANDS:-250000}"

if [[ -z "${POKER_ACCESS_PASSWORD:-}" ]]; then
  echo "Set POKER_ACCESS_PASSWORD first, e.g.:"
  echo "  export POKER_ACCESS_PASSWORD='your-secret'"
  echo "  ./run_online.sh"
  exit 1
fi

exec python3 app.py
