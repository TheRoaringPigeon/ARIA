# AWS Migration

A sibling tracking doc to [`scaling-debt.md`](scaling-debt.md), split out of
it 2026-08-18. Same spirit and conventions (a tracking list, not a plan —
each item needs its own design decision before it's fixed, same as
`qol-backlog.md`), but a different filter on what belongs here:

**`scaling-debt.md`** is for items that help target a future cloud
deployment *and* can be built and verified against the local/self-hosted
Docker Compose stack today — no AWS account required to finish or test them.

**This doc** is for items that inherently need a live AWS environment to
actually complete and verify — wiring real AWS Secrets Manager/SSM, a real
ECS/Fargate service definition and autoscaling policy, a real ALB, and so
on. There's no local stand-in for these; they can be *planned* now but not
finished or proven correct until there's an AWS account and running infra to
configure them against.

Status legend: 🔴 not started · 🟡 partially addressed · 🟢 fixed

---

## 1. 🔴 AWS secrets-manager story

**Moved from `scaling-debt.md` #13** (2026-08-18) — original item, unchanged
except for renumbering context.

**Where:** `.gitignore` correctly excludes `.env`/`.env.*` (only
`.env.example`/`.env.prod.example` are tracked — verified, no real secret
committed). `docker-compose.prod.yml` sources every credential from plain
`${VAR}` substitution against a `.env.prod` file sitting on the host.

**Why it matters:** No leak today, but "plaintext env file on one host,
loaded via `--env-file`" doesn't map onto AWS secret-injection patterns
(ECS task secrets, Secrets Manager, SSM Parameter Store) — worth planning
before the migration, not after.

**Why it lives here, not `scaling-debt.md`:** there's no local equivalent of
"configure an ECS task definition to pull a secret from Secrets Manager at
container start." Deciding *which* mechanism to use (Secrets Manager vs. SSM
Parameter Store — cost and rotation-support tradeoff) can happen without
AWS in hand, but wiring it up and confirming a container actually receives
the secret at boot needs a real ECS task running against a real secret
store.

**Severity:** blocks-AWS-launch for real production hygiene; not urgent
otherwise — today's plaintext-`.env.prod`-on-one-host setup has no known
leak and isn't getting worse on its own.

---

## 2. 🔴 ECS/Fargate service scaling config

**Split out of `scaling-debt.md` #24** ("No horizontal-scaling story for the
worker or core-api") 2026-08-18 — see that item for the part of this that's
already fixable and testable locally (Celery concurrency tuning, proving the
worker is safe to run as N stateless containers against one Redis broker,
multiple `core-api` replicas behind Caddy).

**Where:** eventually, an ECS/Fargate service (or task) definition per
service (`core-api`, `worker`, `ai-service`) — doesn't exist yet, since
there's no ECS deployment at all today.

**Why it matters:** `scaling-debt.md` #24 can prove *locally* that running
N worker containers against the same Redis broker, or N `core-api`
containers behind a load balancer, is safe and correctly tuned. It can't
prove that the AWS-specific mechanism for actually doing that in
production — `desired-count`, a target-tracking or step autoscaling policy,
health-check-driven task replacement — is configured correctly, because
that mechanism doesn't exist outside of a real ECS cluster.

**Why it lives here, not `scaling-debt.md`:** same reason as #1 above —
writing the task-definition JSON can be drafted without AWS, but "does this
autoscaling policy actually add/remove tasks the way I expect under load" is
only answerable against a real ECS service.

**Severity:** blocks-AWS-launch for "real multiple households," but only
once the app is actually on ECS — no current urgency while the only
deployment target is a single self-hosted host.

---

## How to use this document

1. Same rule as `scaling-debt.md`: items are independent, pick up any one
   without sequencing against the others.
2. Nothing here should be started as real implementation work until there's
   an actual AWS account/environment to build and test against — until
   then, these are planning-only (deciding *which* AWS mechanism to use,
   drafting config), not shippable.
3. When an item ships, flip its status marker and add a one-line closing
   note, same convention as `scaling-debt.md`/`roadmap.md`/`qol-backlog.md`.
4. If a `scaling-debt.md` item turns out, on closer look, to only be
   completable with AWS infra in hand, move it here — cleanly, with no
   reference left behind in `scaling-debt.md` — rather than leaving it
   stalled there indefinitely.
