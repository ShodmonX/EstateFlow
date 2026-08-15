# Sprint 4 Search and Bot Handoff

Sprint 4 introduces one typed search contract: `estateflow.search.v1`.
Both the REST API, Telegram bot wizard, saved filters, and future notification
matching should use `SearchCriteria`.

Search decisions:

- User-facing search returns only `status = active` canonical parents.
- Monthly rent search excludes `price_period = one_time`.
- Price filters use `price_normalized_monthly`.
- Daily prices are already normalized to 30 days in that field.
- `per_person` listings are hidden by default. When included, their normalized
  value is still per-person and is not converted to total.
- Audience matching is flat and direct: requested tag must be in
  `audience_tags` and absent from `audience_excluded_tags`.
- Amenities are not filterable in Sprint 4.

Saved filters persist a `SavedFilterPayload` wrapper with the same
`SearchCriteria` payload so Sprint 5 notifications can read filters without
interpreting free-form JSON.

Saved filter CRUD:

- `user_filters.filters` stores `SavedFilterPayload(schema_version,
  criteria)`; repositories validate it back into `SearchCriteria` on read.
- Backward compatibility policy: legacy rows that contain raw `SearchCriteria`
  JSON without the wrapper are accepted on read, then any update rewrites the
  wrapped `estateflow.saved_filter.v1` format.
- Bot callbacks are owner-bound. A user can list, view, run, edit, enable,
  disable, confirm-delete, and delete only their own filters.
- Editing is a replacement flow: open a saved filter, choose edit, run/adjust
  the wizard, then press "Filtrni yangilash"; the same typed criteria is saved.
- Notification worker in Sprint 5 should consume only enabled filters and
  should call the same `SearchCriteria.model_validate(...)` contract.
