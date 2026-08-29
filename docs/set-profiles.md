# Set profiles

A set profile is an optional, local artifact containing evidence for one set and
one Limited event format. The profile boundary is in `draftomen.set_profile`.
It is separate from the card database and 17Lands cache formats; those existing
cache files are not migrated or rewritten by profile loading.

## Schema version 1

A profile is a JSON object with these required fields:

- `schema_version`: exactly `1`. A future value is rejected rather than guessed.
- `profile_version`: the producer's non-empty artifact version.
- `set_code` and `format`: the exact target set and event format.
- `generated_at`: an ISO-8601 timestamp.
- `maturity`: `mature`, `early`, `metadata-only`, or `semantic-only` for local
  artifacts. The in-memory generic fallback is marked `generic` and is never
  written as an evidence artifact.
- `confidence`: a finite number from `0` through `1`.
- `source`: an object with a non-empty `provider` and optional artifact metadata.

Empirical sections are sparse and optional:

- `samples`, when present, contains a non-negative `total` and an optional
  `by_pair` object. The map contains only observed configured pairs; an omitted
  map means that per-pair counts are unavailable. `SampleSummary.count_for()`
  returns `None` for an unavailable pair.
- `pair_profiles`, when present, is an array containing only configured pairs
  for which a context is available. Each pair is listed at most once, in
  canonical `config.COLOR_PAIRS` order. A missing context is exposed by
  `SetProfile.pair()` returning `None`, not by an empty fabricated context.
  A context can contain optional empirical `structural_targets`, `role_targets`,
  `removal_targets`, `synergy`, and `scarcity` arrays. Empty arrays mean no
  evidence was supplied and are omitted during canonical serialization.

Pair-profile semantic annotations are separate from empirical evidence:

- `theme`, when present, is a trimmed, non-empty descriptive label for a pair
  context. It is semantic annotation metadata, not evidence, and may be used
  in semantic-only profiles. It annotates the canonical pair for explanations
  and is never a whitelist: a theme does not make other canonical pairs
  ineligible.

Mature and early profiles must contain empirical evidence. Metadata-only
profiles must contain neither empirical nor semantic evidence. Semantic-only
profiles must carry the compiled `role_profile`, may carry semantic pair
annotations such as `theme`, and must not contain empirical evidence. Thus
metadata-only artifacts can omit `samples` and `pair_profiles`, while
semantic-only artifacts omit empirical sections but retain `role_profile` and
may retain theme annotations. The generic fallback contains neither empirical
section.

The optional `role_profile` object carries compiled per-card semantic roles. It
uses the existing semantic-role vocabulary and assignment types, and declares
`schema_version`, `role_schema_version`, `classifier_version`, and `cards`.
Unknown optional fields are ignored so producers can add fields without making
older readers unsafe. Required fields, field types, finite numeric values,
color-pair coverage where supplied, role assignments, maturity evidence
invariants, and all version values are validated. Serialization sorts keys and
records, so equivalent profiles have stable bytes. The domain graph consists of
frozen dataclasses and tuples; parsed JSON is not retained as mutable
dictionaries or lists.

## Lifecycle and local loading

Profile files are local only. The default path is:

```text
<app-data>/set-profiles/<set-code>-<format>.json
```

Here `<app-data>` means the directory returned by `app_data_dir()` (normally
`~/.draftomen`); `set_profile_path()` does not append `.draftomen` a second
time.

Use `load_set_profile(path)` when a caller needs strict failure. Use
`safe_load_set_profile(set_code, event_format, ...)` at a live boundary. A safe
load never returns JSON and always returns one `SetProfileLoadResult`:

1. choose the valid local `mature` profile;
2. otherwise choose a valid local `early` profile;
3. otherwise choose a valid local `semantic-only` profile;
4. otherwise choose a valid local `metadata-only` profile;
5. otherwise use a supplied `last_valid_profile` only if its set and format both
   match the requested target;
6. otherwise return a zero-confidence immutable `generic` profile for exactly the
   requested set and format.

Missing, corrupt, future-schema, malformed, and wrong-target candidates become
diagnostics instead of exceptions. Candidate directories may be supplied for
fixture or application-specific discovery, but selection remains maturity-first
and deterministic. No remote fetch is attempted.

## Semantic-role compatibility

Compiled role data is authoritative only when its declared role schema and
classifier version match the installed `ROLE_SCHEMA_VERSION` and
`CLASSIFIER_VERSION`. `SetProfile.resolve_roles()` passes the compiled object
unchanged to `RoleClassifier.resolve`, which performs the compatibility check
and falls back to local classification. An incompatible profile therefore falls
back wholly to the local classifier; roles are never merged across versions.
Missing empirical sections do not remove a valid semantic role profile.

## Deliberate exclusions

Profiles do not change pick scoring, deck-building heuristics, card-cache
schemas, or 17Lands cache schemas. They do not fetch or publish data. Producers
remain responsible for generating evidence and choosing maturity; the loader
only validates, orders, and safely exposes it.
