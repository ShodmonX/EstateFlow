# Testing

From a Python 3.12+ environment with `pip install -e ".[dev]"`:

```powershell
.venv\Scripts\python.exe -m pytest -ra
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src
.venv\Scripts\python.exe scripts\smoke_check.py
.venv\Scripts\python.exe scripts\evaluate_source_parser.py
```

`unit`, `integration`, and `e2e` markers select layers; some files carry more than one layer marker. Domain markers include ingestion, AI extraction, pre/post-AI dedup, search, notifications, migrations and observability. Most integration/E2E coverage uses deterministic in-memory services. `tests/conftest.py` clears selected external credentials and blocks external sockets unless a test explicitly opts into `allow_network`.

## Infrastructure checks

Docker-backed runtime and Alembic tests create disposable containers and skip if the daemon is unavailable. The migration test exercises upgrade, parser downgrade/re-upgrade, schema inspection and legacy bridging. Never redirect tests to a production database.

Set `RABBITMQ_TEST_URL` to a disposable local broker to enable `tests/test_rabbitmq_queue.py`. It exercises publish, acknowledge, retry and terminal DLQ delivery using unique queue names. It leaves broker topology behind, so use test infrastructure. Runtime tests also cover a local Redis/PostgreSQL stack when Docker is available.

The [CI workflow](../.github/workflows/ci.yml) runs Ruff, MyPy and pytest on Python 3.12 with a RabbitMQ service and explicit synthetic credentials. PostgreSQL/runtime tests launch their own Docker containers when available. No coverage percentage or production load claim is implied.

## Parser evaluation

`scripts/evaluate_source_parser.py` defaults to `tests/data/source_parser_replay.json`. It prints labeled candidate precision/recall and decision/count accuracy and exits nonzero when supplied thresholds fail. The small synthetic dataset is a regression fixture; validate real policies on separately labeled, privately held data. Write temporary benchmark outputs under ignored `benchmark-results/` or `.test-tmp/`.

The [flow matrix](sprint_7_test_matrix.md) and [AI input contract](sprint_2_ai_handoff.md) retain their established filenames because regression tests read them. Their contents are engineering references, not sprint plans.
