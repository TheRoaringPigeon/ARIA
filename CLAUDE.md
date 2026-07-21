# ARIA

## MCP: aria-household-ops

`services/ai-service/app/mcp_server.py` runs a standalone FastMCP server
(docker-compose service `mcp-server`, profile `mcp`, port 8003) exposing two
write tools: `create_log` and `create_schedule`. It's registered with Claude
Code as `aria-household-ops` (`http://localhost:8003/mcp`, local/project MCP
config — `claude mcp list` to check).

Both tools take an explicit `session_cookie: str` argument — they don't do
their own auth. To call them:

1. Run `scripts/aria-mcp-login.sh` — logs in as the `Claude` service account
   (a `member`-role user in the household, created via the invite flow) using
   credentials in `.env.aria-mcp` (gitignored, not in git history), and
   prints a fresh `aria_session` cookie value on stdout.
2. Pass that value as the `session_cookie` argument to `create_log` or
   `create_schedule`.

The service account is scoped to whatever household it was invited into —
`member` role can create/update/archive/restore logs, schedules, and
entities there, but not hard-delete (owner-only). Session cookies from step 1
are valid 7 days, but just re-run the script each time rather than caching —
it's cheap and avoids expiry bugs.

If `create_log`/`create_schedule` aren't showing up as callable tools, the
MCP connection loads at session start — start a new session.
