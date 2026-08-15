from __future__ import annotations

import re
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.config import Config

from alembic import command
from estateflow.application.core.config import Settings, get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAFE_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_alembic_ini() -> Path:
    candidates = [
        Path.cwd() / "alembic.ini",
        Path("/app/alembic.ini"),
        PROJECT_ROOT / "alembic.ini",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path.cwd() / "alembic.ini"


@dataclass(frozen=True)
class AlembicRuntime:
    config: Config
    schema: str | None = None


def build_alembic_config(
    settings: Settings | None = None,
    *,
    schema: str | None = None,
) -> AlembicRuntime:
    resolved = settings or get_settings()
    normalized_schema = normalize_schema_name(schema)
    ini_path = resolve_alembic_ini()
    config = Config(str(ini_path))
    config.set_main_option("sqlalchemy.url", resolved.sync_database_url)
    if not config.get_main_option("script_location"):
        script_dir = ini_path.parent / "alembic"
        if script_dir.is_dir():
            config.set_main_option("script_location", str(script_dir))
        else:
            config.set_main_option("script_location", "/app/alembic")
    config.cmd_opts = Namespace(x=_x_arguments(normalized_schema))
    return AlembicRuntime(config=config, schema=normalized_schema)


def upgrade_head(settings: Settings | None = None, *, schema: str | None = None) -> None:
    runtime = build_alembic_config(settings, schema=schema)
    command.upgrade(runtime.config, "head")


def current(settings: Settings | None = None, *, schema: str | None = None) -> None:
    runtime = build_alembic_config(settings, schema=schema)
    command.current(runtime.config, verbose=True)


def stamp_head(
    settings: Settings | None = None,
    *,
    schema: str | None = None,
    dry_run: bool = False,
) -> None:
    runtime = build_alembic_config(settings, schema=schema)
    if dry_run:
        return
    command.stamp(runtime.config, "head")


def _x_arguments(schema: str | None) -> list[str]:
    if schema is None:
        return []
    return [f"schema={schema}"]


def normalize_schema_name(schema: str | None) -> str | None:
    if schema is None:
        return None
    normalized = schema.strip()
    if not normalized:
        return None
    if not SAFE_SCHEMA_RE.fullmatch(normalized):
        raise ValueError(f"Invalid schema name: {schema}")
    return normalized


def safe_database_url(settings: Settings | None = None) -> str:
    resolved = settings or get_settings()
    return resolved.sync_database_url


def settings_from_env() -> Settings:
    return get_settings()


def config_option(name: str, default: Any = None) -> Any:
    return getattr(get_settings(), name, default)
