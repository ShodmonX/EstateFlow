from __future__ import annotations

import os


def main() -> None:
    os.environ.setdefault("WORKER_STAGE", "notification")
    from estateflow.workers.serve import main as run_worker

    run_worker()


if __name__ == "__main__":
    main()
