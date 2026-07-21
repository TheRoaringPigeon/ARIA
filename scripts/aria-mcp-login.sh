#!/usr/bin/env bash
# Logs in as the "Claude" MCP service account and prints a fresh
# aria_session cookie value on stdout (nothing else — safe to capture in
# a variable). Reads credentials from .env.aria-mcp at the repo root.
#
# Usage: session_cookie=$(scripts/aria-mcp-login.sh)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$repo_root/.env.aria-mcp"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file — see conversation history for how it was created." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

response=$(curl -s -i -X POST "${ARIA_CORE_API_URL}/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${ARIA_MCP_EMAIL}\",\"password\":\"${ARIA_MCP_PASSWORD}\"}")

cookie=$(printf '%s' "$response" | grep -i '^set-cookie:' | sed -n 's/.*aria_session=\([^;]*\).*/\1/p')

if [[ -z "$cookie" ]]; then
  echo "Login failed — response was:" >&2
  echo "$response" >&2
  exit 1
fi

printf '%s' "$cookie"
