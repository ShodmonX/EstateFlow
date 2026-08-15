# Launch Gate

## Objective

Production launch faqat quyidagi shartlar bajarilganda boshlanadi.

## Required gate

- `pytest` full suite pass.
- `ruff check` pass.
- `mypy` pass for `src/` and approved test baseline.
- Real env validation completed without printing secrets.
- Backup and restore path proven.
- Telethon session storage permissions verified.
- Feature flags default-safe and rollback-ready.
- Admin endpoints protected by `x-admin-token`.
- Health endpoints green in staging-like environment.

## Exit criteria

- No P0 blockers.
- P1 blockers either closed or accepted by owner with written signoff.
- Release owner, backup owner, and incident owner assigned.

