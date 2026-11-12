# EstateFlow V2 — Final isolated migration report

**Outcome:** FULLY VERIFIED OFFLINE / PARTIALLY VERIFIED / BLOCKED (choose evidence-backed one)
**V1 production changes:** NONE expected; confirm with evidence.
**Commit/branch:** ...
**Isolation project:** ...

## 1. What runs

| Component | Service command / container | Schema / role | Health | Feature status | Test evidence |
|---|---|---|---|---|---|

## 2. Test results

| Suite | Command | Exit code | Passed | Failed | Skipped | Evidence |
|---|---|---:|---:|---:|---:|---|

Include the executed FND-01–34 plus all cases in V2_ACCEPTANCE_MATRIX.md. JSON file must contain case-level truth, not estimated successes.

## 3. V1 parity

One row per audited V1 route and behavior: source pointer, V2 owner, WORKING / MOCK_ONLY / DEFERRED / MISSING, reason and test ID. Overall percentage only if denominator is transparent.

## 4. Crash, retry, identity and schema access

Summarize actual kill-point runs, inbox/outbox invariants, role negative tests and failed/recovered scenarios. Include timeouts, test limitations, and whether genuine PostgreSQL/RabbitMQ containers were used.

## 5. Open D01–D08 production decisions

List local provisional selections, alternatives, risk and user decisions required **before** any deployment with real credentials/data.

## 6. Unresolved issues and next repair prompts

Priority P0/P1/P2; symptom; exact path:line; reproducible failure; recommended action; cost/risk; ready-to-copy focused Codex follow-up prompts. Don't silently omit incomplete areas.

## 7. Changes and safety

Exact file groups, tests/commands, `git diff --check`, `git status --short`, no V1/network/production impact confirmation, image tags/digests, no host ports and volume cleanup policy.

## 8. How to run

Exact observed clean-room Compose build/launch/migrate/test commands, expected output and troubleshooting. Never list commands that were not validated as validated.
