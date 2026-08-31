# Set profiles

A set profile is an immutable, versioned artifact containing evidence for one
set and one Limited event format. The profile boundary is in
`draftomen.set_profile`. It is separate from the card database and 17Lands
cache formats; those existing cache files are not migrated or rewritten by
profile loading.

The producer workflow is explicit and reproducible: it reads caller-selected
local inputs, writes a validated compressed artifact plus a generation marker,
and can turn those outputs into a remote manifest record. The client workflow
is offline-first and network-optional; no profile host or production manifest
is bundled with Draft Omen.

## Generate producer artifacts

`generate-profile` is the producer-side workflow for generating a profile from
local inputs. It never downloads a card database, ratings, or draft dump. Run
it through the installed terminal entry point:

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

### Producer API for a remote manifest

The producer APIs are explicit about the handoff from local generation to
remote publication:

1. `generate_local_profile_artifacts(...)` returns a validated
   `ProfilePublicationResult` containing the compressed artifact, generation
   report, and aggregate counts.
2. `profile_manifest_artifact_from_publication(result, artifact_url)` validates
   that result and converts it to one `ProfileManifestArtifact`. The supplied
   URL is the location where the producer will serve that exact compressed
   artifact.
3. `build_profile_manifest(artifacts, published_at=...)` builds the canonical
   `ProfileManifest` for one or more set/format artifacts.
4. `publish_profile_manifest(path, manifest)` writes the canonical manifest
   atomically. `load_profile_manifest(path)` and `dump_profile_manifest(...)`
   provide strict local manifest I/O.

Remote publication is intentionally a separate operator/deployment concern.
Draft Omen does not ship a hosted endpoint, upload job, schedule, signing
workflow, or production manifest URL. Issue #227 owns hosting and publication
automation; a producer must arrange serving the manifest and the exact
artifact URLs before clients can refresh.

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

Historical-pick modeling, benchmark calibration, promotion gates, and other
runtime-integration signals proposed in #88 remain outside this profile
contract.

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

## Remote manifest schema version 1

The producer manifest is a canonical JSON object with exactly these top-level
fields:

- `schema_version`: exactly `1`.
- `published_at`: a timezone-aware ISO-8601 publication timestamp.
- `artifacts`: a non-empty array with at most one artifact for each normalized
  `set_code` and `format` pair.

Each `artifacts` item has exactly these fields:

- `set_code` and `format`: non-empty, case-folded safe path components.
- `set_profile_schema_version`: exactly the supported set-profile schema (`1`).
- `profile_version`: a non-empty producer version.
- `generated_at`: a timezone-aware ISO-8601 timestamp.
- `url`: an absolute HTTPS URL for the compressed profile artifact.
- `gzip_bytes` and `profile_bytes`: positive integer compressed and
  decompressed sizes.
- `gzip_sha256` and `profile_sha256`: 64-hex-character SHA-256 digests of
  the exact compressed and decompressed bytes.
- `maturity`: `mature`, `early`, `semantic-only`, or `metadata-only`;
  `generic` is never a downloadable manifest artifact.

Unknown fields, duplicate set/format identities, future schema versions,
invalid timestamps, unsafe path components, bad sizes/digests, and non-HTTPS
URLs are rejected. Manifest and profile serialization is canonical (sorted
keys and records with a final newline), so equivalent values have stable
bytes. `ProfileManifest.from_json()` and `load_profile_manifest()` perform
strict validation; `ProfileManifest.select(set_code=..., event_format=...)`
only returns an exact normalized identity.

## Lifecycle, cache, and refresh

Profile use is local-first and network-optional. The authoritative profile
cache is always the flat path:

```text
<app-data>/set-profiles/<set-code>-<format>.json
```

Here `<app-data>` means the directory returned by `app_data_dir()` (normally
`~/.draftomen`); `set_profile_path()` does not append `.draftomen` a second
time. The client keeps a separate validated manifest envelope at
`<app-data>/set-profiles/v1/manifest.json`; that file is only a manifest
validation/TTL cache, not a profile source. Downloaded profiles are stored as
canonical, uncompressed JSON at the flat path.

`ProfileClient(app_dir=..., manifest_url=..., network_policy=...)` is the
public client boundary. `ProfileNetworkPolicy.OFFLINE` forbids network access;
`ProfileNetworkPolicy.ALLOWED` permits it only when a manifest URL is
configured. `load_cached(set_code, event_format)` performs local I/O only and
never constructs a request. `refresh(set_code, event_format, force=False,
network_policy=...)` returns a `ProfileRefreshResult` containing the usable
profile, optional diagnostics/manifest, and a compact `status`.

For ordinary candidate loading, `safe_load_set_profile(...)` never raises for
missing, corrupt, future-schema, malformed, or wrong-target candidates. Its
deterministic fallback hierarchy is mature, then early, semantic-only, and
metadata-only (the evidence-backed shorthand is mature → semantic/early →
generic), then a supplied `last_valid_profile` whose set and format match, and
finally a zero-confidence generic profile for exactly the requested target.
`load_scoring_profile(...)` returns `None` instead of exposing that generic
fallback to scoring.

`ProfileClient.load_cached()` first uses a valid non-generic flat profile. If
that destination is missing, corrupt, future-schema, wrong-target, or generic,
it checks historical locations from earlier versions:

```text
<app-data>/profiles/<set>-<format>.json
<app-data>/set-profiles/v1/profiles/<set>-<format>.json
<app-data>/set-profiles/v1/<set>-<format>.json
```

A valid non-generic historical profile is reused offline and, when the flat
destination is writable, migrated there under the per-profile lock; the
historical file remains untouched. This preserves profiles across upgrades
without requiring a remote manifest. Corrupt or future-schema files are
reported as diagnostics and are not automatically deleted. A refresh failure
never deletes or replaces the last-good flat profile (or generic fallback); a
newly fetched valid manifest may still be cached independently before an
artifact failure is reported.

When networking is allowed, the client reuses a validated manifest for its
default 24-hour TTL unless `force=True`, then selects the exact normalized
set/format artifact. It accepts only a newer maturity or timestamp; an
identical artifact is `unchanged`, while an older, lower-maturity, or
same-timestamp conflicting artifact is `stale-manifest`. A missing target
artifact is `missing`. Manifest and artifact failures never replace the
current profile.

Every remote URL must be absolute HTTPS with no credentials, fragment,
whitespace, or non-default port. Artifact URLs and redirects must remain on
the manifest URL's HTTPS origin. The client applies a positive timeout (10
seconds by default), bounds the manifest to 1 MiB, compressed artifacts to
64 MiB, and decompressed profiles to 128 MiB. It streams the gzip bytes,
checks the declared compressed size and SHA-256, rejects incomplete or
trailing gzip data, checks decompressed size and SHA-256, then validates
canonical JSON, set-profile schema, set/format identity, profile version,
maturity, generated timestamp, and schema version before installation.

Staging files are created in the destination directory. A successful commit
flushes and atomically replaces the flat cache with `os.replace`, then
flushes the directory; a failed download, decompression, checksum, schema, or
metadata check cannot become current. Concurrent refreshes for the same cache
key use a process-local lock and re-read the destination under that lock, so a
later commit cannot overwrite a newer valid profile with an older one.

Refresh outcomes are compact and stable:

```text
offline | cached | unchanged | updated | missing | stale-manifest |
manifest-invalid | artifact-invalid | remote-failed
```

`offline` means no usable cached profile was available when networking was
disabled; `cached` means a non-generic local profile was retained without
networking; `updated` means a newer artifact was committed. The remaining
outcomes describe why a remote refresh did not replace the cache. In every
case the result's `profile` is the usable last-good or generic fallback.
The manual command prints this as compact maturity/outcome status:

```sh
uv run draftomen-tui refresh-profile \
  --set-code TST \
  --format quickdraft \
  --manifest-url "$PROFILE_MANIFEST_URL"

# Optional explicit application-data directory:
uv run draftomen-tui refresh-profile \
  --set-code TST \
  --format quickdraft \
  --manifest-url "$PROFILE_MANIFEST_URL" \
  --app-dir "$HOME/.draftomen"
```

Output is one privacy-safe line such as
`refresh-profile: set_code=tst format=quickdraft maturity=mature
outcome=updated cache_path=...`. It does not print profile rows or source
data. A non-generic usable result exits successfully; a generic result or
setup failure exits non-zero.

Live refresh is always an explicit opt-in. Supply the same HTTPS manifest URL
to any live terminal mode:

```sh
draftomen-tui watch --profile-manifest-url "$PROFILE_MANIFEST_URL"
draftomen-tui watch --plain --profile-manifest-url "$PROFILE_MANIFEST_URL"
```

The desktop live command accepts the same opt-in:

```sh
draftomen --profile-manifest-url "$PROFILE_MANIFEST_URL"
```

Without `--profile-manifest-url`, TUI, plain-watch, and desktop live scoring
remain offline and use local/historical caches only. TUI and desktop expose
compact maturity/outcome status (for example `mature · updated`); failure
status does not discard the profile already used for scoring.

## Semantic-role compatibility

Compiled role data is authoritative only when its declared role schema and
classifier version match the installed `ROLE_SCHEMA_VERSION` and
`CLASSIFIER_VERSION`. `SetProfile.resolve_roles()` passes the compiled object
unchanged to `RoleClassifier.resolve`, which performs the compatibility check
and falls back to local classification. An incompatible profile therefore falls
back wholly to the local classifier; roles are never merged across versions.
Missing empirical sections do not remove a valid semantic role profile.

## Deliberate exclusions

The generator and client do not change pick scoring, deck-building heuristics,
card-database schemas, or 17Lands cache schemas. The generator reads only
explicit local inputs; the producer manifest APIs validate and write local
publication files, while the client downloads only when explicitly configured.
Neither side hosts, uploads, schedules, signs, or discovers a production
manifest. Issue #227 owns hosting and publication automation.
Producers remain responsible for generating evidence, choosing maturity, and
serving the exact checksummed artifacts described by their manifest; the
loader only validates, orders, and safely exposes profiles.
