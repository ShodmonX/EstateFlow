from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Engine, inspect, text

from estateflow.db.migration_tools import normalize_schema_name
from estateflow.db.schema_contract import (
    EXPECTED_AUDIENCE_TAG_SEEDS,
    EXPECTED_CHECK_CONSTRAINT_NAMES,
    EXPECTED_INDEX_NAMES,
    EXPECTED_TABLES,
)
from estateflow.models import Base


@dataclass(frozen=True)
class ColumnFingerprint:
    name: str
    type: str
    nullable: bool
    default: str | None


@dataclass(frozen=True)
class IndexFingerprint:
    name: str
    unique: bool
    columns: tuple[str, ...]
    where: str | None = None


@dataclass(frozen=True)
class TableFingerprint:
    name: str
    columns: tuple[ColumnFingerprint, ...]
    primary_key: tuple[str, ...]
    unique_constraints: tuple[tuple[str, ...], ...]
    foreign_keys: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...]
    indexes: tuple[IndexFingerprint, ...]
    checks: tuple[str, ...]


@dataclass(frozen=True)
class SchemaFingerprint:
    tables: dict[str, TableFingerprint]
    audience_tags: dict[str, str]
    index_names: tuple[str, ...]
    check_names: tuple[str, ...]


@dataclass(frozen=True)
class SchemaDrift:
    table: str
    missing_columns: tuple[str, ...] = ()
    extra_columns: tuple[str, ...] = ()
    column_issues: tuple[str, ...] = ()


def inspect_schema(engine: Engine, *, schema: str | None = None) -> SchemaFingerprint:
    inspector = inspect(engine)
    resolved_schema = normalize_schema_name(schema)
    tables: dict[str, TableFingerprint] = {}
    for table_name in inspector.get_table_names(schema=resolved_schema):
        columns = tuple(
            ColumnFingerprint(
                name=column["name"],
                type=str(column["type"]).lower(),
                nullable=bool(column["nullable"]),
                default=_normalize_default(column.get("default")),
            )
            for column in inspector.get_columns(table_name, schema=resolved_schema)
        )
        pk = tuple(
            inspector.get_pk_constraint(table_name, schema=resolved_schema)["constrained_columns"]
            or ()
        )
        unique_constraints = tuple(
            tuple(constraint.get("column_names") or ())
            for constraint in inspector.get_unique_constraints(table_name, schema=resolved_schema)
        )
        foreign_keys = tuple(
            (
                fk.get("name") or "",
                fk["referred_table"],
                tuple(fk.get("constrained_columns") or ()),
                tuple(fk.get("referred_columns") or ()),
            )
            for fk in inspector.get_foreign_keys(table_name, schema=resolved_schema)
        )
        indexes = tuple(
            IndexFingerprint(
                name=str(index.get("name") or ""),
                unique=bool(index.get("unique")),
                columns=tuple(cast(str, column) for column in (index.get("column_names") or ())),
                where=_normalize_postgresql_where(cast(Any, index)),
            )
            for index in inspector.get_indexes(table_name, schema=resolved_schema)
        )
        checks = tuple(
            _normalize_check_constraint(cast(Any, item))
            for item in inspector.get_check_constraints(table_name, schema=resolved_schema)
        )
        tables[table_name] = TableFingerprint(
            name=table_name,
            columns=columns,
            primary_key=pk,
            unique_constraints=unique_constraints,
            foreign_keys=foreign_keys,
            indexes=indexes,
            checks=checks,
        )

    audience_tags = _read_seed_tags(engine, schema=resolved_schema)
    index_names = tuple(
        sorted(
            {
                index.name
                for table in Base.metadata.sorted_tables
                for index in getattr(table, "indexes", set())
            }
            | set(EXPECTED_INDEX_NAMES)
        )
    )
    check_names = tuple(
        sorted(
            {
                check.get("name") or ""
                for table in EXPECTED_TABLES
                for check in inspector.get_check_constraints(table, schema=resolved_schema)
                if check.get("name")
            }
            | set(EXPECTED_CHECK_CONSTRAINT_NAMES)
        )
    )
    return SchemaFingerprint(
        tables=tables,
        audience_tags=audience_tags,
        index_names=index_names,
        check_names=check_names,
    )


def compare_schema(engine: Engine, *, schema: str | None = None) -> list[SchemaDrift]:
    inspector = inspect(engine)
    resolved_schema = normalize_schema_name(schema)
    drift: list[SchemaDrift] = []
    observed_tables = set(inspector.get_table_names(schema=resolved_schema))
    expected_tables = set(EXPECTED_TABLES)

    extra_tables = tuple(sorted(observed_tables - expected_tables - {"alembic_version"}))
    if extra_tables:
        drift.append(SchemaDrift(table="__extra_tables__", extra_columns=extra_tables))

    for table in EXPECTED_TABLES:
        if table not in observed_tables:
            drift.append(SchemaDrift(table=table, missing_columns=("__table__",)))
            continue
        observed_columns = {
            column["name"] for column in inspector.get_columns(table, schema=resolved_schema)
        }
        expected_columns = {column.name for column in Base.metadata.tables[table].columns}
        missing_columns = tuple(sorted(expected_columns - observed_columns))
        extra_columns = tuple(sorted(observed_columns - expected_columns))
        if missing_columns or extra_columns:
            drift.append(
                SchemaDrift(
                    table=table,
                    missing_columns=missing_columns,
                    extra_columns=extra_columns,
                )
            )
        if table == "announcements" and "is_promoted" in observed_columns:
            observed = {
                column["name"]: {
                    "nullable": bool(column["nullable"]),
                    "default": _normalize_default(column.get("default")),
                }
                for column in inspector.get_columns(table, schema=resolved_schema)
            }["is_promoted"]
            expected_column = Base.metadata.tables[table].columns["is_promoted"]
            expected_nullable = bool(expected_column.nullable)
            expected_default = _normalize_model_default(expected_column.server_default)
            issues: list[str] = []
            if observed["nullable"] != expected_nullable:
                issues.append(
                    f"is_promoted nullable expected {str(expected_nullable).lower()} "
                    f"observed {str(observed['nullable']).lower()}"
                )
            if observed["default"] != expected_default:
                issues.append(
                    f"is_promoted default expected {expected_default or 'null'} "
                    f"observed {observed['default'] or 'null'}"
                )
            if issues:
                drift.append(SchemaDrift(table=table, column_issues=tuple(issues)))

    observed_seed_tags = _read_seed_tags(engine, schema=resolved_schema)
    missing_seeds = tuple(sorted(set(EXPECTED_AUDIENCE_TAG_SEEDS) - set(observed_seed_tags)))
    if missing_seeds:
        drift.append(
            SchemaDrift(
                table="audience_tag_types",
                missing_columns=tuple(f"seed:{tag}" for tag in missing_seeds),
            )
        )

    observed_index_names = {
        index["name"]
        for table in EXPECTED_TABLES
        if table in observed_tables
        for index in inspector.get_indexes(table, schema=resolved_schema)
        if index.get("name")
    }
    missing_indexes = tuple(sorted(set(EXPECTED_INDEX_NAMES) - observed_index_names))
    if missing_indexes:
        drift.append(
            SchemaDrift(
                table="__indexes__",
                missing_columns=missing_indexes,
            )
        )

    observed_check_names = {
        check["name"]
        for table in EXPECTED_TABLES
        if table in observed_tables
        for check in inspector.get_check_constraints(table, schema=resolved_schema)
        if check.get("name")
    }
    missing_checks = tuple(sorted(set(EXPECTED_CHECK_CONSTRAINT_NAMES) - observed_check_names))
    if missing_checks:
        drift.append(
            SchemaDrift(
                table="__checks__",
                missing_columns=missing_checks,
            )
        )

    return drift


def format_drift(drift: list[SchemaDrift]) -> str:
    lines: list[str] = []
    for item in drift:
        if item.table == "__extra_tables__":
            lines.append(f"extra tables: {', '.join(item.extra_columns)}")
            continue
        if item.table == "__indexes__":
            lines.append(f"missing indexes: {', '.join(item.missing_columns)}")
            continue
        if item.table == "__checks__":
            lines.append(f"missing checks: {', '.join(item.missing_columns)}")
            continue
        if item.missing_columns == ("__table__",):
            lines.append(f"missing table: {item.table}")
            continue
        if item.missing_columns:
            lines.append(f"{item.table} missing columns: {', '.join(item.missing_columns)}")
        if item.extra_columns:
            lines.append(f"{item.table} extra columns: {', '.join(item.extra_columns)}")
        if item.column_issues:
            lines.append(f"{item.table} column issues: {', '.join(item.column_issues)}")
    return "\n".join(lines)


def schema_matches(engine: Engine, *, schema: str | None = None) -> bool:
    return not compare_schema(engine, schema=schema)


def _read_seed_tags(engine: Engine, *, schema: str | None = None) -> dict[str, str]:
    resolved_schema = normalize_schema_name(schema)
    with engine.connect() as connection:
        try:
            if resolved_schema:
                connection.execute(text(f"set search_path to {resolved_schema}, public"))
            rows = connection.execute(
                text(
                    """
                    select tag_key, display_name_uz
                    from audience_tag_types
                    where status = 'approved'
                    order by tag_key
                    """
                )
            ).all()
        except Exception:
            return {}
    return {str(row.tag_key): str(row.display_name_uz) for row in rows}


def _normalize_default(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _normalize_model_default(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "arg"):
        value = value.arg
    if value is None:
        return None
    return str(value).strip().lower()


def _normalize_postgresql_where(index: Any) -> str | None:
    dialect_options = index.get("dialect_options") or {}
    where = dialect_options.get("postgresql_where") if isinstance(dialect_options, dict) else None
    if where is None:
        return None
    return str(where).strip().lower()


def _normalize_check_constraint(item: Any) -> str:
    name = item.get("name") or ""
    sqltext = str(item.get("sqltext") or "").strip().lower()
    return f"{name}:{sqltext}"
