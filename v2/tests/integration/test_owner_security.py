import os
import psycopg
import pytest
from v2.foundation import Store
from v2.shared.routing import OWNERS


@pytest.mark.integration
def test_all_context_runtime_roles_deny_every_foreign_schema():
    if "EFV2_DSN_LISTENER" not in os.environ:
        pytest.skip("private Compose environment required")
    for owner in OWNERS:
        store = Store(owner)
        with store.transaction() as tx:
            tx.put("security_probe", "synthetic", {"id": "synthetic", "owner": owner})
        for foreign in OWNERS:
            if foreign == owner:
                continue
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with store.transaction() as tx:
                    tx.conn.execute(f"SELECT * FROM {foreign}.records LIMIT 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with store.transaction() as tx:
                tx.conn.execute("CREATE TABLE public.synthetic_forbidden(id int)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with store.transaction() as tx:
                tx.conn.execute(f"SET ROLE ef_{owner}_owner")
