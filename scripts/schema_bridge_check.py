from __future__ import annotations
# ruff: noqa: E402,I001

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from estateflow.db.migration_cli import main

if __name__ == "__main__":
    main(["bridge-check", *sys.argv[1:]])
