# Quality Gates Plan

## Objective

Code va test quality productionga chiqish uchun yetarli ekanini hard gate bilan belgilash.

## Required checks

- `ruff check .`
- `pytest`
- `mypy src tests`
- Smoke check with fake dependencies
- Health endpoint tests
- Admin auth tests
- Release controls tests

## Cleanup items

- Dependency deprecation warninglarni rejalashtirish.
- Duplicate marker descriptions housekeeping.

## Exit criteria

- CI all green.
- No new warning or flaky test pattern introduced.
- Approved baseline documented.
