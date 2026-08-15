-- Sprint 4 search and saved-filter contract hardening.
-- Additive indexes only; safe to run more than once.

create index if not exists ix_announcements_sprint4_search_filters
    on announcements(district, rooms, renovation_level, created_at desc)
    where parent_announcement_id is null
      and status = 'active'
      and price_period in ('daily', 'monthly');

create index if not exists ix_announcements_audience_excluded_tags_gin
    on announcements using gin(audience_excluded_tags);

create index if not exists ix_user_filters_user_created
    on user_filters(user_id, created_at desc);
