# Source parser policies

EstateFlow routes every Telegram raw event by `source_id` before Pre-AI deduplication.
The listener is transport-only: one Telegram client is created per `(adapter, session)`,
while each event carries its own versioned parser binding.

```text
Telegram/album -> SourceParserRouter -> drop | durable quarantine | 0..N candidates
               -> Pre-AI dedup -> Primary AI -> quality gate -> optional Preview
```

## Binding fields

Each `ingestion_sources` row has:

- `parser_key`: reusable recipe or allowlisted plugin key;
- `parser_version`: immutable rollout/version label;
- `parser_config`: validated JSON recipe overrides, maximum 32 KiB;
- `parser_mode`: `active`, `shadow`, or `disabled`.

`parser_key` and `parser_version` are binding identity. They must be supplied in the
outer source binding and are rejected inside `parser_config`, preventing the stored
admin identity from differing from the recipe used at runtime.

`source_profile` remains only as a migration compatibility field. `default` maps to
`generic.single_listing`; `caption_first` and `album_text_merge` map to
`generic.album_caption`. Unknown legacy values fail safely to the generic single parser.

Built-in recipes are `generic.single_listing`, `generic.digest_blocks`,
`generic.album_caption`, and `generic.mixed_feed`. A source-specific recipe composes
the same cleanup, split, filter, scoring and media operators:

```json
{
  "parser_key": "source.example.digest",
  "parser_version": "3",
  "parser_mode": "shadow",
  "parser_config": {
    "family": "digest_blocks",
    "cleanup": {
      "strip_patterns": ["(?im)^reklama uchun.*$"]
    },
    "split": {
      "patterns": ["(?m)(?=^\\s*#?\\d+[.)]\\s+)"],
      "max_candidates": 20
    },
    "filters": {
      "include_any": ["(?i)\\b(?:kvartira|xonadon|hovli)\\b"],
      "exclude_any": ["(?i)\\b(?:ish qidiraman|reklama|kurs)\\b"],
      "unmatched_policy": "quarantine",
      "excluded_policy": "drop"
    },
    "score": {
      "minimum": 0.45,
      "below_minimum_policy": "quarantine"
    },
    "media_policy": "auto",
    "defaults": {"listing_type": "rent", "price_period": "monthly"},
    "required_fields": ["price", "district"],
    "prompt_hints": ["Each numbered block is one independent listing."]
  }
}
```

`required_fields` also controls Preview retry. Values are checked after schema
normalization, so malformed phones or empty lists do not count as present. Missing
required data can trigger Preview; if every model still misses it, the best valid
response is retained for manual review and can never auto-publish. Explicit non-rental
or contradictory rental classification stops without spending a Preview call.

Recipe objects reject unknown fields, unsafe/invalid regular expressions, and hidden
identity fields before an admin/bootstrap binding is persisted. A custom Python parser
is allowed only through an explicitly registered plugin key; arbitrary import paths are
never loaded from configuration.

## Rollout and testing

- `active`: parser output controls `drop`, `quarantine`, or candidate fan-out.
- `shadow`: parser output is audited, while the original compatibility candidate
  continues through the pipeline.
- `disabled`: parser is bypassed and the compatibility candidate is emitted.

Use `POST /admin/source-parser/preview` to inspect cleanup, decisions and candidate
splitting without DB, queue or AI side effects. Use the labeled replay gate locally:

```powershell
.venv\Scripts\python.exe scripts\evaluate_source_parser.py
```

The command accepts another JSON dataset path and `--min-*` quality thresholds. It
reports candidate precision/recall, decision accuracy, drop accuracy and exact
candidate-count accuracy, returning exit code `1` when a threshold is missed. Content
precision/recall only uses candidates with explicit expected text; count-only labels
cannot inflate those metrics.

Production rollout should start in shadow mode with 100-300 labeled posts per source,
then move through a deterministic canary before activation. Monitor
`source_parser_decision` by source/key/version together with actual Preview retry and
manual-review rates. Randomly audit a small sample of drops to measure false negatives.

## Provenance and fail-safes

Every AI candidate has a stable ID/idempotency key plus raw event/message/media
provenance. Fan-out candidates also get a persistence identity derived from the raw
Telegram message ID and candidate index, so sibling listings do not collide in dedup or
the database and the same slot remains stable across edits. This slot mapping is
intentionally marked reorder-sensitive in diagnostics. Digest candidates do not
automatically reuse an ambiguous album on every block; `media_policy` must opt into
that behavior.

An active `quarantine` never becomes an acknowledged hard drop. The raw event (or the
specific rejected digest segment) is durably published to
`ingestion.source_parser.quarantine` with parser key/version, reason codes and stable
quarantine identity. A queue publish failure retries the original raw message.

A stateful `buffer` decision is exposed for custom/thread policies, but the current
deployment has no persistent cross-message parser buffer. Such a decision is therefore
audited and fails open to the original compatibility candidate
(`buffer_unavailable_fail_open`) so a Telegram event cannot be silently lost.
