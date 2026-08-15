# Production Cutover Plan

## Objective

Beta'dan productionga o'tish paytidagi oxirgi amallarni xavfsiz bajarish.

## Cutover steps

1. Final backup create.
2. Release owner approval record.
3. Feature flags review.
4. Production deployment.
5. Health check validation.
6. Admin smoke checks.
7. Real traffic enablement in phases.
8. Rollback watch window start.

## Go/no-go checklist

- Backup verified.
- Restore path known.
- Owners on call.
- Alerts active.
- Monitoring dashboards open.
- Rollback command ready.

