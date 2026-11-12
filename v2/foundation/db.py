"""Schema-qualified PostgreSQL persistence. Runtime clients never bootstrap roles."""

import os
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from v2.shared.contracts import canonical_hash, validate

OWNERS = tuple(
    "listener source_parser pre_ai ai catalog search notifications identity control taxonomy content analytics lab_producer lab_consumer".split()
)


class Store:
    def __init__(self, owner, dsn=None):
        if owner not in OWNERS:
            raise ValueError("Unknown schema owner")
        self.owner = owner
        self.dsn = dsn or os.environ.get("EFV2_DSN") or os.environ[f"EFV2_DSN_{owner.upper()}"]
        info = conninfo_to_dict(self.dsn)
        if (
            os.environ.get("EFV2_MODE", "FAKE_ONLY") != "FAKE_ONLY"
            or info.get("host") != "postgres"
            or info.get("dbname") != "estateflow_v2_lab"
        ):
            raise PermissionError("Database access restricted to isolated FAKE_ONLY lab")

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            identity = conn.execute("SELECT current_user AS name").fetchone()
            if identity is None:
                raise PermissionError("Runtime identity unavailable")
            role = identity["name"]
            if role != f"ef_{self.owner}_app":
                raise PermissionError("Runtime role does not match owner")
            yield Transaction(self, conn)

    def scan(self, kind):
        with self.transaction() as tx:
            return tx.scan(kind)

    def parked(self):
        with self.transaction() as tx:
            return tx.conn.execute(
                f"SELECT * FROM {self.owner}.parked_events ORDER BY created_at"
            ).fetchall()

    def retries(self):
        with self.transaction() as tx:
            return tx.conn.execute(
                f"SELECT * FROM {self.owner}.retry_schedule ORDER BY next_attempt_at"
            ).fetchall()

    def metrics(self):
        with self.transaction() as tx:
            result = {}
            for table in ("outbox", "inbox", "retry_schedule", "parked_events"):
                result[table] = tx.conn.execute(
                    f"SELECT count(*) AS n FROM {self.owner}.{table}"
                ).fetchone()["n"]
            result["pending_outbox"] = tx.conn.execute(
                f"SELECT count(*) AS n FROM {self.owner}.outbox WHERE status <> 'published'"
            ).fetchone()["n"]
            result["oldest_pending_seconds"] = tx.conn.execute(
                f"SELECT coalesce(extract(epoch FROM now()-min(created_at)),0)::float AS age FROM {self.owner}.outbox WHERE status IN ('pending','leased')"
            ).fetchone()["age"]
            result["expired_leases"] = tx.conn.execute(
                f"SELECT count(*) AS n FROM {self.owner}.outbox WHERE status='leased' AND lease_until<now()"
            ).fetchone()["n"]
            return result


class Transaction:
    def __init__(self, store, conn):
        self.store, self.conn, self.owner = store, conn, store.owner

    def lock(self, key):
        self.conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (self.owner + ":" + key,)
        )

    def get(self, kind, key):
        self.lock("record:" + kind + ":" + str(key))
        row = self.conn.execute(
            f"SELECT body FROM {self.owner}.records WHERE kind=%s AND key=%s FOR UPDATE",
            (kind, str(key)),
        ).fetchone()
        return row["body"] if row else None

    def put(self, kind, key, body):
        self.lock("record:" + kind + ":" + str(key))
        self.conn.execute(
            f"INSERT INTO {self.owner}.records(kind,key,body) VALUES(%s,%s,%s) ON CONFLICT(kind,key) DO UPDATE SET body=EXCLUDED.body",
            (kind, str(key), Jsonb(body)),
        )

    def scan(self, kind):
        return [
            r["body"]
            for r in self.conn.execute(
                f"SELECT body FROM {self.owner}.records WHERE kind=%s ORDER BY key", (kind,)
            ).fetchall()
        ]

    def emit(self, event):
        validate(event)
        if event["producer"] != self.owner:
            raise PermissionError("Event producer must match transaction owner")
        digest = canonical_hash(event)
        existing = self.conn.execute(
            f"SELECT fingerprint FROM {self.owner}.outbox WHERE event_id=%s", (event["event_id"],)
        ).fetchone()
        if existing:
            if existing["fingerprint"] != digest:
                raise ValueError("Immutable outbox event conflict")
            return
        self.conn.execute(
            f"INSERT INTO {self.owner}.outbox(event_id,event,fingerprint,routing_key) VALUES(%s,%s,%s,%s)",
            (
                event["event_id"],
                Jsonb(event),
                digest,
                event["event_type"] + ".v" + str(event["event_version"]),
            ),
        )


TABLES = """
CREATE TABLE IF NOT EXISTS {o}.schema_versions(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
INSERT INTO {o}.schema_versions(version) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS {o}.records(kind text NOT NULL,key text NOT NULL,body jsonb NOT NULL,PRIMARY KEY(kind,key));
CREATE TABLE IF NOT EXISTS {o}.outbox(event_id uuid PRIMARY KEY,event jsonb NOT NULL,fingerprint text NOT NULL,routing_key text NOT NULL,status text NOT NULL DEFAULT 'pending',attempts integer NOT NULL DEFAULT 0,next_attempt_at timestamptz NOT NULL DEFAULT now(),lease_token uuid,lease_until timestamptz,error_class text,created_at timestamptz NOT NULL DEFAULT now(),published_at timestamptz);
CREATE INDEX IF NOT EXISTS outbox_due ON {o}.outbox(status,next_attempt_at);
CREATE TABLE IF NOT EXISTS {o}.inbox(event_id uuid PRIMARY KEY,fingerprint text NOT NULL,status text NOT NULL,processed_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS {o}.revisions(stream text NOT NULL,version integer NOT NULL,event_id uuid NOT NULL,PRIMARY KEY(stream,version));
CREATE TABLE IF NOT EXISTS {o}.retry_schedule(event_id uuid PRIMARY KEY,event jsonb NOT NULL,attempts integer NOT NULL,next_attempt_at timestamptz NOT NULL,error_class text NOT NULL,status text NOT NULL DEFAULT 'pending',lease_token uuid,lease_until timestamptz);
ALTER TABLE {o}.retry_schedule ADD COLUMN IF NOT EXISTS transport_attempts integer NOT NULL DEFAULT 0;
INSERT INTO {o}.schema_versions(version) VALUES(2) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS {o}.parked_events(id uuid PRIMARY KEY,event_id text,event jsonb,reason text NOT NULL,error_class text,created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz,replayed_at timestamptz);
CREATE TABLE IF NOT EXISTS {o}.replay_audit(id uuid PRIMARY KEY,parked_id uuid NOT NULL,actor text NOT NULL,mode text NOT NULL,allowed boolean NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
"""


def migrate():
    """Privileged clean-lab bootstrap only; existing schema contents are preserved."""
    from psycopg import sql

    dsn = os.environ["EFV2_ADMIN_DSN"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        if conn.info.dbname != "estateflow_v2_lab" or conn.info.host != "postgres":
            raise PermissionError("Migration restricted to isolated lab database")
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        conn.execute("REVOKE TEMPORARY ON DATABASE estateflow_v2_lab FROM PUBLIC")
        for owner in OWNERS:
            owner_role, app_role, migrate_role = (
                f"ef_{owner}_{suffix}" for suffix in ("owner", "app", "migrate")
            )
            for role, login, password in [
                (owner_role, False, None),
                (app_role, True, os.environ[f"EFV2_PASSWORD_{owner.upper()}"]),
                (migrate_role, True, os.environ[f"EFV2_MIGRATE_PASSWORD_{owner.upper()}"]),
            ]:
                if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                    conn.execute(
                        sql.SQL(
                            "CREATE ROLE {} {} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                        ).format(sql.Identifier(role), sql.SQL("LOGIN" if login else "NOLOGIN"))
                    )
                if password:
                    conn.execute(
                        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(password)
                        )
                    )
                conn.execute(
                    sql.SQL("ALTER ROLE {} SET search_path TO pg_catalog, {}").format(
                        sql.Identifier(role), sql.Identifier(owner)
                    )
                )
            conn.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(owner_role), sql.Identifier(migrate_role)
                )
            )
            conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                    sql.Identifier(owner), sql.Identifier(owner_role)
                )
            )
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner_role)))
            try:
                conn.execute(TABLES.format(o=owner))
                conn.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(owner), sql.Identifier(app_role)
                    )
                )
                conn.execute(
                    sql.SQL(
                        "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                    ).format(sql.Identifier(owner), sql.Identifier(app_role))
                )
                conn.execute(
                    sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                        sql.Identifier(owner), sql.Identifier(app_role)
                    )
                )
                conn.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}"
                    ).format(sql.Identifier(owner), sql.Identifier(app_role))
                )
                conn.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT USAGE,SELECT ON SEQUENCES TO {}"
                    ).format(sql.Identifier(owner), sql.Identifier(app_role))
                )
                conn.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
                    ).format(sql.Identifier(owner))
                )
            finally:
                conn.execute("RESET ROLE")
