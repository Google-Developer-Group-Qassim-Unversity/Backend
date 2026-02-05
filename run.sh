#!/usr/bin/env bash
set -euo pipefail

RED="\033[31m"; GREEN="\033[32m"; YELLOW="\033[33m"; BLUE="\033[34m"; CYAN="\033[36m"; RESET="\033[0m"

info() { printf "%b%s%b\n" "${CYAN}ℹ️  " "$1" "${RESET}"; }
ok()   { printf "%b%s%b\n" "${GREEN}✅ " "$1" "${RESET}"; }
err()  { printf "%b%s%b\n" "${RED}❌ " "$1" "${RESET}"; }

ENV_PATH="${ENV_PATH:-.env}"

if [[ ! -f "$ENV_PATH" ]]; then
  err "Missing .env at '$ENV_PATH'."; exit 1
fi

set -a # automatically export varibles
source "$ENV_PATH"
set +a

if [[ -z "${DATABASE_URL:-}" ]]; then
  err "DATABASE_URL is missing or empty."; exit 2
fi

env_mode="${ENV:-Production}"
case "$env_mode" in
  development|Development)
    info "Running in DEVELOPMENT mode"
    ;;
  *)
    ok "Running in PRODUCTION mode"
    ;;
esac

uv run uvicorn app.main:app --reload --port 7001 --workers 2
