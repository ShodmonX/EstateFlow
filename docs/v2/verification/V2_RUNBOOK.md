# Isolated V2 runbook

As built/tested 2026-11-10. All providers FAKE_ONLY. Do not use root V1 Compose, real provider credentials, live Telegram or production databases. No host ports are published. Commands below run from /home/deploy/projects/EstateFlow. Docker daemon operations require the platform's normal permission review.

The original estateflow-v2-isolated project is stopped, with volumes preserved. Final verification uses fresh estateflow-v2-isolated-verify20261110, its own private internal network and named PostgreSQL/RabbitMQ volumes. Its ignored infra/v2/.env.verification.local is generated synthetic data, mode0600. Never print/commit either env file. Old lab credentials/cache must not be reused for real data.

## Provision and inspect

These expanded Compose arguments were used successfully; define a shell function for convenience (the function itself makes no infrastructure changes):

```bash
cd /home/deploy/projects/EstateFlow
python3 scripts/v2/lab-secrets.py --output .env.verification.local
v2lab() {
  docker compose --env-file infra/v2/.env.verification.local \
    -f infra/v2/compose.yaml -p estateflow-v2-isolated-verify20261110 "$@"
}
v2lab config --quiet
v2lab build migrate
v2lab up -d postgres rabbitmq migrate
```

Generator preserves an existing file and never replaces credentials. For another empty lab, use a new estateflow-v2-isolated-verify… project name; never remove existing volumes for a clean test. Reusing a credential file for a new project is synthetic lab convenience only. EFV2_ENV_FILE selects the test/bootstrap env_file; runtime owners receive only their own DSN and lab HTTP/broker keys.

```bash
v2lab run --rm --no-deps v2-test-runner python -m v2.foundation.cli migrate
v2lab run --rm --no-deps v2-test-runner python -m v2.foundation.cli inspect --owner catalog
python3 scripts/v2/inspect-lab.py
```

Migration is additive and repeatable, versions1–2 for 14 owners. This is a privileged whole-lab bootstrap, not an independent owner migration release command. Inspect emits counts/ages, not payloads/secrets. The inspector reads only the selected project's container IDs and writes sanitized metadata to ignored .artifacts/v2/final/isolation.json. Do not run unrestricted docker inspect/config output into reports because it can expose environment values.

## Tests before starting domain workers

The pipeline harness owns temporary queues and expects no automatic sender/competing domain workers. On an already running lab, stop the 17 worker/adapter services first; preserve PostgreSQL/RabbitMQ and all volumes. Do not run restart injection concurrently with other DB/broker tests.

```bash
v2lab run --rm --no-deps v2-test-runner python -m pytest --tb=short \
  -p no:cacheprovider -c /app/v2/pytest.ini /app/v2/tests/foundation -q
v2lab run --rm --no-deps v2-test-runner python -m pytest --tb=short \
  -p no:cacheprovider -c /app/v2/pytest.ini /app/v2/tests/integration -q
python3 scripts/v2/lab-faults.py --project estateflow-v2-isolated-verify20261110 \
  --env-file infra/v2/.env.verification.local
python3 scripts/v2/lab-restore.py --project estateflow-v2-isolated-verify20261110 \
  --env-file infra/v2/.env.verification.local
```

Fault driver genuinely restarts/stops only the selected lab broker/DB, restores the broker in finally, and asserts durable recovery. Restore driver uses privileged synthetic-only pg_dump/psql, creates a new estateflow_v2_restore_<random> database in this lab cluster, compares112 table count/content digests, and preserves the restored database. It does not prove offhost/PITR recovery or production grants reconstruction.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/v2/verify-contracts.py
docker run --rm --network none --read-only --tmpfs /tmp estateflow-v2-lab:dev \
  python -m pytest --tb=short -p no:cacheprovider -c /app/v2/pytest.ini /app/v2/tests/unit -q
docker run --rm --network none --read-only --tmpfs /tmp estateflow-v2-lab:dev \
  python -m ruff check --no-cache --config /app/v2/pyproject.toml /app/v2
docker run --rm --network none --read-only --tmpfs /tmp estateflow-v2-lab:dev \
  python -m mypy --config-file /app/v2/pyproject.toml --cache-dir /tmp/mypy /app/v2
docker run --rm --network none --read-only --tmpfs /tmp estateflow-v2-lab:dev python -m pip check
```

Python host needs only standard library for strict JSON; schema checks use jsonschema only if already installed. Lab image supplies pinned tools. No host/global install. RefResolver and Starlette/anyio deprecation warnings are known. pip check is dependency consistency, not a vulnerability audit. No browser automation/live-provider tests are implied.

## Deploy and test HTTP

```bash
v2lab up -d --no-build
v2lab run --rm --no-deps -e EFV2_DEPLOYED_TESTS=1 v2-test-runner \
  python -m pytest --tb=short -p no:cacheprovider -c /app/v2/pytest.ini /app/v2/tests/e2e -q
python3 scripts/v2/inspect-lab.py
v2lab restart search
```

Twelve owner workers, notification-sender, public-api, admin-api, internal-api and bot are independent processes. Each has internal :8000 health endpoints; e2e reaches them over the private network, signs only synthetic WebApp data, creates filter/source inputs, and verifies fake send/edit/delete/error results. Public/admin static consoles are available inside the network; no local browser port override was created or tested. Use the containerized e2e client. Full V1 UI/response compatibility remains incomplete.

Restart search alone preserves its event archive/inbox/projections. For a code deployment, rebuild the image and select only the owner with up -d --no-deps <owner>; use immutable release tags and backward-compatible owner migrations before real deployment. Only independent restart is actually exercised here; this is not a complete rolling upgrade/release pipeline.

## Recovery and operators

- Inspect owner metrics with the foundation CLI above; inspect parked bodies only in an authorized synthetic lab session. Poison bytes are not retained. Pending outbox/error class/age/retries/parked rows are owner-local; DLQ queues end in .dead. Consumer queues are v2.<owner>.events.v1 with bindings in v2/shared/routing.py.
- Rebuild search by an authenticated admin POST /commands/search with action=rebuild, subject_id=synthetic-projection, arguments={}; then poll /operations/search/<id>. This rebuilds only from search's retained catalog event archive. If the archive is absent it fails explicitly; complete catalog snapshot export/resubscription bootstrap is future work.
- For a known synthetic parked UUID, CLI replay syntax is python -m v2.foundation.cli replay --owner <owner> --parked-id <uuid> --actor lab-operator inside v2-test-runner. It retains body/event ID, resets a terminal retry budget and audits authorization; it never deletes inbox history. The CLI end-to-end user flow was not separately exercised; underlying authorized/denied replay was tested against real PostgreSQL and retry publication against RabbitMQ. Do not treat the actor string as production RBAC.
- UNKNOWN notifications/content require explicit resolve_unknown with actor/reason/outcome. Automatic resend is forbidden. Failed source-approval saga requires manual reward reconciliation. Empty successful reparse replacements require catalog review_batch approval/reason before old evidence is removed.
- Generic owner commands use scoped HTTP actor tokens and async operation status. Generated lab admin tokens are test-only; there is no production admin authentication interface. Shared broker principal is an unresolved security boundary.

## Stop and rollback

```bash
v2lab stop
```

Stop preserves all volumes/accepted state. The original project was stopped this way and retained. No volume deletion, global prune, downgrade/stamp, commits/pushes or V1 operation was performed. Do not run down -v automatically. Rollback real cutover is a design-only gate: keep V1 authoritative, stop V2 consumers/adapters, preserve inbox/outbox/evidence, reconcile owner snapshots and notification history before any later traffic migration. No actual production cutover or rollback is tested.
