# Trophy-draft pick benchmark

Draft Omen can benchmark pick recommendations against 17Lands public draft data without replaying Arena logs. The workflow is intended for calibration, not for claiming that every trophy pick was objectively correct.

## Offline workflow

1. Download a public 17Lands draft-data dump from <https://www.17lands.com/public_datasets>, for example `draft_data_public.TMT.PremierDraft.csv.gz`.
2. Make sure 17Lands ratings for the same set/format are cached, or add `--refresh-ratings` when you explicitly want the command to refresh them.
3. Run the benchmark against the local dump:

   ```bash
   uv run draftomen-tui benchmark-picks \
     --set-code TMT \
     --format PremierDraft \
     --draft-data-file path/to/draft_data_public.TMT.PremierDraft.csv.gz
   ```

   You do not need to provide a Scryfall bulk file in normal use. Draft Omen loads or refreshes its own card-metadata cache automatically. `--bulk-file path/to/scryfall-default-cards.jsonl` is only for deterministic/offline runs where you already downloaded Scryfall's `default_cards` bulk JSONL yourself.

Run at least two set/format pairs before changing recommendation defaults again, for example one PremierDraft dump and one TradDraft dump:

```bash
uv run draftomen-tui benchmark-picks --set-code TMT --format PremierDraft --draft-data-file path/to/draft_data_public.TMT.PremierDraft.csv.gz
uv run draftomen-tui benchmark-picks --set-code TMT --format TradDraft --draft-data-file path/to/draft_data_public.TMT.TradDraft.csv.gz
```

The command filters to trophy drafts by default: seven wins for Premier/Quick-style drafts and three wins for Traditional drafts. Use `--include-non-trophy` only for exploratory comparisons.

Full public draft dumps are large, so the scoring step can take a few minutes. For a quick smoke test before the full run, add `--max-drafts 200`:

```bash
uv run draftomen-tui benchmark-picks \
  --set-code TMT \
  --format PremierDraft \
  --draft-data-file path/to/draft_data_public.TMT.PremierDraft.csv.gz \
  --refresh-ratings \
  --max-drafts 200
```

## Reported metrics

The report compares raw 17Lands WR ranking with Draft Omen `DO Score` ranking and prints:

- top-1, top-3, and top-5 match rates for the actual trophy pick;
- average actual-pick rank, where lower is better;
- the same metrics broken down by pick-engine phase: `open`, `building`, and `locked`;
- direct DO-vs-17L rank deltas showing how often DO ranked the trophy pick better, the same, or worse;
- skipped-row reasons for unresolved card names or incomplete pack data.

## Default ranking decision

DO Score is the default ranking. The decision is based on trophy benchmarks across two sets and three set/formats where DO Score improved top-1/top-3/top-5 match rate and average actual-pick rank versus raw 17L WR:

| Set/format | Trophy drafts | Picks | 17L top-1 | DO top-1 | 17L top-3 | DO top-3 | 17L top-5 | DO top-5 | 17L avg rank | DO avg rank |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TMT PremierDraft | 3,404 | 139,564 | 44.9% | 48.5% | 78.8% | 82.6% | 91.8% | 93.3% | 2.44 | 2.26 |
| TMT TradDraft | 998 | 40,918 | 45.4% | 54.1% | 78.6% | 84.2% | 91.7% | 93.8% | 2.44 | 2.13 |
| SOS PremierDraft | 10,888 | 457,294 | 45.5% | 48.7% | 78.6% | 81.4% | 91.1% | 92.2% | 2.46 | 2.32 |

The open-pick phase remains a caution area: DO Score was slightly worse than 17L WR on TMT and SOS PremierDraft open picks and tied 17L WR on TMT TradDraft open picks. The TUI therefore keeps active ranking copy visible and shows early/close-pick confidence copy instead of overclaiming certainty. Users can still press `s` to switch to raw 17L WR.

## Miss review and non-ML calibration

Benchmark misses should be reviewed before further default or scoring changes. The first non-ML heuristic to inspect is the color commitment ramp and off-color penalty: all current reports showed many building/locked DO Score misses where trophy drafters still took off-color cards. Other non-ML follow-ups include pair-specific pick priorities and maindeck-rate weighting from the same public draft data.

This calibration is intentionally separate from #37. The benchmark can tune transparent heuristics now; ML-based recommendations should use these reports as baseline evidence rather than replacing them. See [ML-based pick recommendations](ml-pick-recommendations.md) for the historical-data research design, leakage rules, evaluation gate, and future integration boundary.

