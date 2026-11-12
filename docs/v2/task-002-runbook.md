# Task 002 — Isolated Foundation Runbook (Codex must finalize after implementation)

**Status:** command templates only. The Compose file and CLI do not yet exist. Replace placeholders with actual implementation paths and tested commands before claiming the task is complete.

## Safety preflight

```bash
cd ~/projects/EstateFlow
pwd
git status --short
docker compose version
# Review the dedicated Compose project before running it:
docker compose -f infra/v2/foundation/compose.yaml -p estateflow-v2-foundation config --services
```

Confirm no service joins V1 networks, no host ports, no host Docker socket mount, and no existing named volume. Do not paste `.env` secrets into chat or commit them. `docker compose config` can render secrets from environment; redact before sharing its output.

## Bring up disposable lab

```bash
docker compose -f infra/v2/foundation/compose.yaml -p estateflow-v2-foundation up -d --build

docker compose -f infra/v2/foundation/compose.yaml -p estateflow-v2-foundation ps
```

## Run tests (replace `tests` with actual Compose test service)

```bash
docker compose -f infra/v2/foundation/compose.yaml -p estateflow-v2-foundation run --rm tests
```

Use project-scoped `docker compose exec` for DB/broker inspection, or test-runner CLI. Avoid publishing management UI/DB ports to the host just for convenience.

## Verify no host-published ports

```bash
docker compose -f infra/v2/foundation/compose.yaml -p estateflow-v2-foundation ps
# Also inspect the resolved Compose YAML and container PortBindings; do not rely on ps alone.
```

## Restart/failure exercise

Codex must supply scripts that target only this Compose project and lab service names. Use injected test fault hooks where possible. Any `kill` or restart must be confined to lab containers; never use generic process names or system-wide commands.

## Stop (preserve lab volumes for inspection)

```bash
docker compose -f infra/v2/foundation/compose.yaml -p estateflow-v2-foundation down
```

**Do not use `down -v` automatically.** After verifying the exact project/volumes and obtaining explicit approval for disposal, a separate cleanup command may remove **only** lab volumes. Never use `docker system prune` or `docker volume prune`.

## Recovery and observability to document

1. Outbox pending/leased/published/terminal counts and oldest pending age.
2. Inbox dedup and aggregate version state.
3. Scheduled retries, next due time, attempt counters, and lease owner/token (redact sensitive fields).
4. Parked/DLQ event count, reasons, replay authorization/audit.
5. Broker queue depth, publisher confirm failures, reconnect and consumer readiness.
6. Tested image versions/digests, migrations and Docker Compose project name.
7. Known limits: single-node broker is not HA; local exactly-once effects do not imply exactly-once external sends.
