from __future__ import annotations

from typing import Any

from estateflow.db.session import DatabaseSessionManager, create_session_manager

__all__ = [
    "DatabaseSessionManager",
    "create_session_manager",
    "current",
    "stamp_head",
    "upgrade_head",
]


def __getattr__(name: str) -> Any:
    if name in {"current", "stamp_head", "upgrade_head"}:
        from estateflow.db import migration_tools

        return getattr(migration_tools, name)
    if name == "LEGACY_SQL_MIGRATIONS":
        from estateflow.db.migrations import LEGACY_SQL_MIGRATIONS

        return LEGACY_SQL_MIGRATIONS
    raise AttributeError(name)
