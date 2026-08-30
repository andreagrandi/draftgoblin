# Set profiles

A set profile is an optional, local artifact containing evidence for one set and
one Limited event format. The profile boundary is in `draftomen.set_profile`.
It is separate from the card database and 17Lands cache formats; those existing
cache files are not migrated or rewritten by profile loading.

## Generate local artifacts

`generate-profile` is the operator-facing, local-only workflow for producing a
profile artifact. It never downloads a card database, ratings, or draft dump.
Run it through the installed terminal entry point:

```sh
uv run draftomen-tui generate-profile \
  --set-code TST \
  --format quickdraft \
  --stage metadata \
  --generated-at 2026-08-30T12:00:00+00:00 \
  --card-database-file "$PWD/.draftomen/inputs/card-database.json" \
  --output-dir "$PWD/.draftomen/set-profiles"
```

The command has these required options:

- `--set-code SET_CODE`: the exact target set code.
- `--format FORMAT`: the exact target event format (for example,
  `quickdraft`).
- `--stage {metadata,early,mature}`: the lifecycle stage to generate. The
  command does not infer or promote a stage from the amount of input data.
- `--generated-at ISO8601`: a timezone-aware ISO-8601 timestamp, such as
  `2026-08-30T12:00:00+00:00`.
- `--card-database-file PATH`: an existing local card-database cache file.
- `--output-dir PATH`: the local directory under which the publication
  directory is created.

The optional input and metadata options are:

- `--ratings-file PATH`: an existing local 17Lands ratings cache file for the
  requested set and format. It is not fetched by this command.
- `--source-manifest PATH`: a local public-dump manifest describing pinned
  local draft data. Its sources must have a local `path` and a SHA-256
  `sha256` pin; a URL-only source cannot be consumed by this workflow.
- `--draft-source-name NAME`: the exact source name to select from the
  manifest. It is required when the manifest contains more than one source
  and is not needed when it contains exactly one.
- `--profile-version VERSION`: the non-empty profile artifact version. It
  defaults to `1.0`.

Set and format inputs are stripped and case-folded for profile, report, and
publication-path identity. A pinned ratings cache must identify the requested
set and format case-insensitively; its stored metadata is retained unchanged.
The set code, format, and every input path are explicit. Keep the card database
and ratings files unchanged when reproducibility matters. A source manifest is
the pin for a draft dump: its `sha256` is the digest of the exact local bytes,
and a relative source `path` is resolved relative to the manifest file. For
example:

```json
{
  "schema_version": 1,
  "sources": [
    {
      "name": "17lands-public-drafts",
      "path": "inputs/tst-drafts.csv.gz",
      "sha256": "REPLACE_WITH_THE_64_HEX_DIGIT_SHA256",
      "retrieved_at": "2026-08-30T11:00:00+00:00",
      "attribution": "Public draft data: 17Lands",
      "license": "Record the terms that apply to this source"
    }
  ]
}
```

Compute the digest over the exact file before replacing the placeholder (on
macOS, `shasum -a 256 "$DUMP_FILE"` prints it). Keep the attribution and
license entries accurate for the source you use; the generator records them
but does not determine or grant rights.

### Lifecycle stages

Use the same explicit set, format, timestamp, card database, and output
directory for each stage. The following commands show the complete progression
for one local input set:

```sh
# 1. No empirical or semantic evidence: metadata-only.
uv run draftomen-tui generate-profile \
  --set-code TST \
  --format quickdraft \
  --stage metadata \
  --generated-at 2026-08-30T12:00:00+00:00 \
  --card-database-file "$PWD/.draftomen/inputs/card-database.json" \
  --output-dir "$PWD/.draftomen/set-profiles"

# 2. Early evidence: use the local ratings cache and one pinned draft source.
uv run draftomen-tui generate-profile \
  --set-code TST \
  --format quickdraft \
  --stage early \
  --generated-at 2026-08-30T12:00:00+00:00 \
  --card-database-file "$PWD/.draftomen/inputs/card-database.json" \
  --ratings-file "$PWD/.draftomen/inputs/tst-quickdraft-ratings.json" \
  --source-manifest "$PWD/.draftomen/inputs/source-manifest.json" \
  --draft-source-name 17lands-public-drafts \
  --output-dir "$PWD/.draftomen/set-profiles"

# 3. Mature evidence: the pinned source must contain accepted decks and
#    Stage C structure targets for every accepted color pair.
uv run draftomen-tui generate-profile \
  --set-code TST \
  --format quickdraft \
  --stage mature \
  --generated-at 2026-08-30T12:00:00+00:00 \
  --card-database-file "$PWD/.draftomen/inputs/card-database.json" \
  --ratings-file "$PWD/.draftomen/inputs/tst-quickdraft-ratings.json" \
  --source-manifest "$PWD/.draftomen/inputs/source-manifest.json" \
  --draft-source-name 17lands-public-drafts \
  --output-dir "$PWD/.draftomen/set-profiles"
```

`metadata` produces a `metadata-only` profile and does not use ratings or
draft rows as profile evidence. `early` produces an `early` profile from
available empirical inputs; the resulting profile must contain empirical
evidence, so generation fails if the supplied inputs cannot provide any.
`mature` requires accepted deck evidence and Stage C structure targets for
every accepted color pair. It fails rather than silently publishing a weaker
profile when those requirements are not met. `semantic-only` is a separate
profile maturity for local loading and is not a `--stage` choice here.

### Publication, validation, and repeatability

For `--set-code TST`, `--format quickdraft`, and the output directory above,
the command writes:

```text
.draftomen/set-profiles/tst-quickdraft/
├── artifacts/
│   └── <gzip_sha256>.json.gz
└── generation.json
```

The exact paths are:

```text
<output>/<set>-<format>/artifacts/<gzip_sha256>.json.gz
<output>/<set>-<format>/generation.json
```

The artifact is the canonical profile JSON compressed with deterministic gzip.
Its filename is the SHA-256 of the compressed bytes, so it is a
content-addressed object. `generation.json` is the authoritative generation
marker: it records the selected set, format, stage, normalized UTC timestamp,
input checksums, source provenance, aggregate counts, profile and gzip
checksums, and sizes. It is the only operation that makes a newly generated
artifact the current generation.

Before publication, the workflow loads only the caller-selected local inputs,
generates the profile, decompresses and parses the gzip artifact, validates the
profile schema and requested set/format, checks canonical serialization, and
reconciles the artifact and report checksums and sizes. It then writes the
content-addressed artifact through a flushed, synchronized temporary file
before atomically replacing the generation marker. An existing artifact is
reused only when its bytes are identical; different bytes at the same
content-addressed path fail.

To regenerate deterministically, repeat the command with identical bytes for
every local input, the same set and format, the same stage and profile version,
and the same explicit timezone-aware `--generated-at` value. Compare the
resulting `gzip_sha256` and `profile_sha256` in `generation.json`; the gzip
checksum must also match the digest in the artifact filename (for example,
`shasum -a 256 <path-from-generation.json>`). The canonical artifact and
generation marker bytes should be identical. If an input or timestamp changes,
the checksums can legitimately change and the prior content-addressed object
is not rewritten.

Successful stdout is aggregate and privacy-safe only: it reports maturity,
input/sample/skip/error counts, `validation=passed`, the artifact path, and
the generation manifest path. It does not print raw rows, source names, card
names, or user identifiers. A workflow failure writes an actionable message
prefixed with `generate-profile:` to stderr and exits with status `1`; it does
not print those data values. Validation failures occur before the marker is
replaced, so the last valid generation remains authoritative. A failed
publication can leave an unreferenced new content object, but it cannot make
an invalid generation the current one.

The successful output uses aggregate key/value lines:

```text
maturity=<metadata-only|early|mature>
input_count=<count>
sample_count=<count>
skip_count=<count>
error_count=<count>
validation=passed
artifact=<artifact-path>
generation_manifest=<generation-json-path>
```

The count values are aggregates from the generation report; the paths point to
the two local publication outputs described above.

### Provenance and legal responsibility

The source manifest is part of the reproducible input record. The generation
report retains each selected source's logical name, SHA-256, retrieval
timestamp when supplied, attribution, and license metadata, while omitting
local paths and raw source rows. It also records canonical checksums for the
card database and ratings data. Keep the source manifest with the inputs used
for regeneration.

The operator or producer remains responsible for obtaining source data lawfully,
preserving required attribution, and recording the applicable license or terms
without inventing them. A report's provenance fields are documentation, not a
license grant. The workflow emits compact aggregate/profile artifacts; it does
not publish the source dump or redistribute raw rows.

This issue does not implement remote publication scheduling (#227) or client
download behavior (#226). It also does not implement the historical-pick model,
benchmark, promotion-gate, or runtime-integration signals proposed in #88;
those remain deferred work.

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

The generator does not change pick scoring, deck-building heuristics,
card-database schemas, or 17Lands cache schemas. It does not fetch input data
or perform remote publication; it only validates and writes local artifacts.
Producers remain responsible for generating evidence and choosing maturity; the
loader only validates, orders, and safely exposes profiles.
