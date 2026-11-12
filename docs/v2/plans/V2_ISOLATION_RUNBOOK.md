# Isolated EstateFlow V2 — installation and operational safety contract

## Default Compose isolation rules

- Compose manifest `infra/v2/compose.yaml` and explicit project `estateflow-v2-isolated`; never use the repo root's legacy docker-compose files for V2 lab commands.
- Own PostgreSQL and RabbitMQ containers, own **named** volumes, own private default network; no shared Docker volumes, host-mounted Telegram session directories, production credentials, host DB sockets, or existing EstateFlow network.
- No default `ports:` mappings; use `docker compose exec` and containerized test clients. If browser UI testing truly requires host access, an additional `infra/v2/compose.local-ui.yaml` may bind **127.0.0.1 only**, must be disabled by default, never expose DB/broker admin credentials, and documented as optional. Avoid broad `0.0.0.0` mappings.
- Generate ignored local lab secret files, e.g. `infra/v2/.env.local` and ignore explicit test output. `.env.example` contains only placeholders, no real tokens. Never commit `.env.local` or passwords.
- External adapters default to `FAKE_ONLY` and fail closed when real endpoint env vars are unexpectedly present. No live Telethon login/connect, aiogram send/poll, OpenRouter HTTP, real object store upload or website scraping. An external adapter may implement an interface and have mock-driven contract tests, but it must never actually connect in this task.
- Container image pulls from registries/package indexes may be necessary at build time; they are **not** equivalent to runtime provider access. Record outbound access actually used. Avoid changing host packages or firewall.

## Suggested exact project commands (adjust to as-built paths)

```bash
cd ~/projects/EstateFlow
export EFV2_COMPOSE='infra/v2/compose.yaml'
docker compose -f "$EFV2_COMPOSE" -p estateflow-v2-isolated config --quiet
docker compose -f "$EFV2_COMPOSE" -p estateflow-v2-isolated up -d --build
# migrations/seeding/execution names are decided and documented by Codex based on actual service names
# e.g. docker compose -f "$EFV2_COMPOSE" -p estateflow-v2-isolated run --rm v2-test-runner ...
docker compose -f "$EFV2_COMPOSE" -p estateflow-v2-isolated ps
docker compose -f "$EFV2_COMPOSE" -p estateflow-v2-isolated down
```

Never run `docker compose down -v` automatically, `docker system prune`, unrestricted `docker volume prune`, delete production files, run `alembic downgrade` against any existing environment, or erase unfamiliar user files. If a test requires empty lab volumes, create a **fresh uniquely named Compose project** or document an explicit human-reviewed lab volume deletion command. The user approved *full implementation*, not destruction of arbitrary host state.

## Data and credentials

- No real source sessions or credentials. Synthetic credentials generated in isolated container/local ignored files. Never use real user contact info, real ads, real Telegram message text or source exports.
- No production backup restore or existing V1 PostgreSQL connection. A synthetic fixture representation of all 26 old tables is sufficient for offline migration rehearsal. Produce a real cutover runbook **without running it**.

## Recovery and evidence

Codex must write and actually verify as-built commands in `docs/v2/verification/V2_RUNBOOK.md`: provision, migration, seed, run worker, run tests, pause/resume broker, restart DB, inspect DLQ and inbox/outbox, replay with audit, projection rebuild, role security audit, backup/restore *lab only*, stop. Include `git status --short`, exact Compose project and observed container list. Logs must be redacted; sanitize any test output before writing docs.

## No continuous user intervention

During a Codex run, proceed phase-to-phase autonomously. User approvals are not required for *lab provisional* D01–D08 decisions; document them. If Docker or shell permissions are denied, Codex must not bypass platform approval/sandbox constraints; proceed with source and tests possible and mark the integration gate BLOCKED. If asked by Codex's host to approve permission, that is a platform permission and is not waived by this document.
