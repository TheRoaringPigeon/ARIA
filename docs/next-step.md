# Next step: one end-to-end check in the chat UI

Committed on `main` (2026-11-25), not yet checked through the UI:

- The local-model rules in `CLAUDE.md` and `docs/local-models.md`.
- The ai-service changes they called for: `decide_action`, per-request
  enums, `num_ctx` with `fit_to_context`, prompt versions, and the
  `scripts/llm-probe.ps1` probes.
- The comment and docstring cleanup in `app/agents/nodes.py` and
  `app/routers/chat.py`.

Unit suite: 275 passed; ruff clean. Live probes: routing 14/14, action
15/15, fuzzy 4/4 (results are in `docs/local-models.md` **Model notes**).

Machine state (not in git):

- AVN's `avn-ollama-1`, `avn-api-1` and `avn-web-1` are stopped, with their
  restart policy set to `no` (it was `unless-stopped`). AVN's
  `docker compose up -d` recreates them with the policy from its compose
  file.
- `aria-llm` and the ARIA dev stack are running.

Steps:

1. In the chat UI (http://localhost:5173), say you changed the oil on a
   vehicle today. Check that the card reads `Log service: ... for <name>,
   <today>`, then cancel it. Do the same for "remind me every 3 months to
   ..." and check the card reads `... every 90 days`.
2. If both look right, empty this file. If not, add the miss as a probe
   case in `services/ai-service/probes/cases.py` before fixing it.
