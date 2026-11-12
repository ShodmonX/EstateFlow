import os
import pytest
import importlib.util
from pathlib import Path


@pytest.mark.integration
def test_synthetic_26_table_compatibility_is_repeatable():
    if "EFV2_DSN_LISTENER" not in os.environ:
        pytest.skip("private lab environment required")
    # Script is loaded as executable utility, not shared domain business code.
    spec = importlib.util.spec_from_file_location(
        "synthetic_backfill", Path("scripts/v2/backfill.py")
    )
    assert spec is not None and spec.loader is not None
    if not spec.origin or not Path(spec.origin).exists():
        pytest.fail("backfill script absent in lab image")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.backfill()
    second = module.backfill()
    assert first == second and len(first) == 26
    assert all(x["id_preserved"] and x["count"] == 1 for x in first.values())
