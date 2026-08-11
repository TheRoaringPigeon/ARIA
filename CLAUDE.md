# ARIA

## Git commits

Never run `git commit` unless the user explicitly asks for that specific
commit, in that turn. An earlier "commit this" in the conversation only
authorizes the commit it was said for — it does NOT carry forward to later
work in the same session, even later work on the same feature/backlog item.
Finishing a task (tests passing, build clean, verified live) is not itself
permission to commit. When work is ready, stop and say so; let the user
review the diff and tell you when to commit.

## Scripts

The user is on Windows — user-run scripts in `scripts/` are PowerShell
(`.ps1`), not bash. Don't add `.sh` scripts there.

## Mongo write patterns

When a code path does N sequential Mongo round trips over a collection of
items (a `for` loop calling `find_one`/`update_one`/`delete_one` per item),
distinguish two different problems before reaching for a fix:

1. **Round-trip cost that scales with N is (almost) always a pure batching
   fix, no infra change needed.** Collapse the loop to a fixed number of
   round trips using `$in`-scoped `update_many`/`delete_many` over the whole
   set at once. Two techniques that come up constantly:
   - **Preserve conditional/race-safety filters by re-applying them inside
     the batch write itself**, not by trusting an earlier snapshot read —
     e.g. `delete_many({"_id": {"$in": ids}, "still_valid_condition": ...})`
     evaluates the condition per-document at write time, same as a per-item
     conditional `delete_one` would, just batched.
   - **When a batch write only returns a count, not which ids matched**
     (`delete_many`/`update_many`), recover "what actually happened" by
     diffing an `_id`-only snapshot taken before against one taken after,
     rather than looping to check each item individually.
2. **Cross-collection atomicity is a different, heavier problem — don't
   reach for it by default.** If the actual requirement is "these N writes
   across different collections must all succeed or all roll back together,"
   that's not a batching problem, it's a transactions problem, and MongoDB
   transactions require the deployment to be a replica set (even a
   single-node one) — a bare standalone `mongod` cannot do them at all, full
   stop. Reaching for real atomicity pulls in real infra surface
   (replica-set conversion, connection URI changes, a prod migration/restart
   step) — treat it with the same weight as any other item with real design
   surface (`EnterPlanMode`-level, not a quick fix). Don't default to it just
   because "transaction" sounds like the correct-by-construction answer —
   check whether the requirement is really atomicity or just round-trip cost
   first.
3. **Test-infra reality check before committing to transactions**: this
   repo's Mongo test doubles (`mongomock_motor` in `core-api`, hand-rolled
   fakes in `worker`) don't support sessions/transactions at all —
   `AsyncMongoMockClient().start_session()` raises `NotImplementedError`.
   Per-call `session=` kwargs are silently accepted everywhere else, so
   route/task business logic still unit-tests fine through a
   dependency-injected no-op transaction override; true rollback-on-failure
   atomicity has to be verified live against the real compose stack, not in
   the unit suite — plan for that up front rather than discovering it
   mid-implementation.

## MCP: aria-household-ops

`services/ai-service/app/mcp_server.py` runs a standalone FastMCP server
(docker-compose service `mcp-server`, profile `mcp`, port 8003) exposing two
write tools: `create_log` and `create_schedule`. It's registered with Claude
Code as `aria-household-ops` (`http://localhost:8003/mcp`, local/project MCP
config — `claude mcp list` to check).

Both tools take an explicit `session_cookie: str` argument — they don't do
their own auth. To call them:

1. Run `scripts/aria-mcp-login.ps1` — logs in as the `Claude` service account
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
