# Semantic analysis corpus

The corpus is development/profile-generation tooling. It is not used by the live
card database and it does not change the application's download or cache schema.
The full Scryfall bulk file is never checked into the repository.

## Build and acquire

From a checkout, the complete broad build (Scryfall default cards, local Arena
mapping files, and one or more MTGJSON set mappings) is:

```sh
uv run draftomen-tui corpus-build \
  --source-spec draftomen/corpus_sources.json \
  --arena-data-dir "$HOME/Library/Application Support/com.wizards.mtga/Downloads/Raw" \
  --mtgjson-set DSK \
  --cache-dir .draftomen/corpus-cache \
  --output-dir .draftomen/corpus-artifacts
```

Use the platform's Arena `Raw` directory on macOS or Windows. Current
`Raw_CardDatabase*.mtga` SQLite files and their
`Raw_ClientLocalization*.mtga` companions are supported, along with legacy
`data_cards*`/`data_loc*` JSON inputs. A local Scryfall fixture can be supplied
with `--scryfall-file`, and
local MTGJSON files with repeatable `--mtgjson-file` options. `--mtgjson-set` is
repeatable. Set codes selected by `--set-code HBL --set-code DSK` switch the
selection mode to explicit; without those options the configured broad policy is
used.

The first run resolves Scryfall's bulk-data endpoint, preferring its current
`jsonl_download_uri` and accepting the legacy `download_uri` field, then downloads
the default-cards URI, copies Arena inputs, and downloads requested MTGJSON set
files. Bytes are written atomically below `.draftomen/corpus-cache/sources/`. The
retrieval `sources.manifest.json` is published before the immutable
`sources.lock.json` commit marker. The lock records source URL/path identity,
SHA-256, version, ETag, attribution, and conservative legal metadata; the
manifest is deliberately separate and records volatile retrieval timestamps as
well. A lock is authoritative on later runs: every cached byte is hashed before
use, and a mismatch fails clearly. The cache and artifacts are ignored by git. A
fully frozen rerun needs no network:

```sh
uv run draftomen-tui corpus-build \
  --source-spec draftomen/corpus_sources.json \
  --cache-dir .draftomen/corpus-cache \
  --output-dir .draftomen/corpus-artifacts \
  --offline
```

Offline mode fails on a required cache miss or checksum mismatch. The workflow
also rejects malformed source specifications and incomplete required Arena pairs.

## Selection and outputs

Broad selection includes the supplied current/default Scryfall corpus, HOB (both
`HOB` and `HBL` spellings), releases from 2018 onward (the Arena-era boundary),
and every record with a supported multi-face layout. Explicit selection is a
case-insensitive set-code allow-list. Selection metadata is embedded in the
coverage report, including candidate count and selected sets.

`normalized.jsonl` is deterministic compact JSONL sorted by set, collector number,
Arena/group ID, and name. Every row contains explicit keys for `arena_id`, `grp_id`,
`set`, `collector_number`, `oracle_id`, `name`, `oracle_text`, `keywords`,
`type_line`, `subtypes`, `layout`, ordered `faces`, `colors`, `mana_cost`,
`mana_value`, `produced_mana`, `rarity`, `source_provenance`,
`source_disagreements`, `unsafe_to_classify`, and `unsafe_reasons`. Unknown values
are `null` (or an empty list where an empty value is meaningful); no values are
invented. Face records retain their own name, Oracle text, keywords, type line,
subtypes, colors, mana, and produced-mana fields in source order.

Coverage treats face-level `oracle_text` and `type_line` as satisfying those
fields only when every face has the value. A missing or `unknown` layout is
unsupported and makes a normalized row unsafe. MTGJSON `type` fills a missing
Scryfall `type_line`; derived subtypes are retained.

Cross-source joins are conservative: an exact Scryfall printing ID wins over
Oracle ID, and Oracle-ID candidates are accepted only after an exact normalized
set-plus-collector match leaves one candidate. Arena group IDs come only from
the Scryfall `arena_id` or the exact matched MTGJSON `mtgArenaId`; name-only
matches and collisions remain unmatched and unsafe.

`coverage.json` is deterministic and reports `missing_arena_ids`,
`missing_semantic_fields`, `unsupported_layouts`,
`wording_mechanic_patterns`, `source_disagreements`, and `unsafe_to_classify`,
plus inspectable details for disagreement and unsafe rows. Retrieval timestamps
never enter these outputs, so identical locked inputs produce byte-identical JSONL
and report files.

## Source terms

The corpus does not claim a separate license for Scryfall card data. Scryfall
API and data use is subject to the [Scryfall API Terms](https://scryfall.com/docs/api)
and the [Wizards Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy).
The policy and any underlying Wizards rights remain applicable to Magic-derived
names, text, and other content; this project does not redistribute Scryfall
images.

MTGJSON is published under its [MIT License](https://github.com/mtgjson/mtgjson/blob/master/LICENSE.txt).
That license covers MTGJSON's contribution and does not grant rights to
underlying Wizards content. Arena client files are local user-provided inputs;
their underlying content remains subject to Wizards terms.

## Tags and offline classifier consumption

Scryfall's [bulk-data documentation](https://scryfall.com/docs/api/bulk-data)
lists separate daily **Art Tags** and **Oracle Tags** bulk files. The
[Tags API documentation](https://scryfall.com/docs/api/tags) describes their
format and says that they contain community-maintained data from the Tagger
project. These mutable tag files are not fields in the `default-cards` file,
so this workflow deliberately excludes them from its inputs.

The cited public API documentation does not establish a separate license for
the tag data. Attribution and licensing claims therefore remain conservative:
the corpus does not redistribute or depend on tag inputs, and classifier
correctness must use normalized card fields and Oracle wording rather than a
tag lookup. Coverage and future classifiers must not treat tags as required
for correctness. The module's `draftomen.corpus.load_normalized_rows(path)` and
`iter_normalized_rows(path)` functions are pure offline loaders: they read the
already-built JSONL and perform no network or acquisition calls. A future
classifier can iterate rows, use `unsafe_to_classify` as a conservative gate,
and consume `source_disagreements` when deciding whether to trust a field.
