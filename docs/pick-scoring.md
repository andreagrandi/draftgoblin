# Pick scoring

Draftgoblin keeps the raw 17Lands GIH win rate visible for every card with a strong games-in-hand sample. The Textual watch table ranks by DG Score by default after TMT PremierDraft, TMT TradDraft, and SOS PremierDraft trophy benchmarks showed better top-1/top-3/top-5 match rates and average actual-pick rank than raw 17L WR. It also keeps raw 17L WR visible and switchable for comparison.

The base rating for `DG` is 17Lands GIH WR when the card has enough games-in-hand samples. If QuickDraft data is missing or thin, the resolver falls back to PremierDraft. If neither format has a strong GIH sample, the card uses a neutral prior, adjusted by ALSA when ALSA is available: earlier ALSA raises the prior, later ALSA lowers it.

Scores are normalized against the set rating distribution and centered so the neutral prior displays as 50 before color logic. Color commitment then multiplies the normalized score: on-color cards rise gradually, off-color cards are penalized gradually, and colorless cards stay neutral.

Pool color weights come from picked cards. Each colored picked card contributes a quality-weighted amount to each of its colors, so a strong card pulls harder than filler. The highest-weighted two-color pair is the inferred pair once at least two colors have material weight.

Commitment is controlled by documented defaults in `config.py`:

- picks 1-5: open, raw scores (`open_pick_count = 5`)
- pick 6: linear ramp begins (`commitment_start_pick = 6`)
- pick 16+: locked to the inferred pair (`locked_pick_index = 16`)
- locked on-color score multiplier: `1.15`
- locked off-color score multiplier: `0.75`
- pool weight baseline/rating scale/min/max: `1.0`, `10.0`, `0.25`, `2.0`

Rows show a `Fit` marker: `On` for cards inside the inferred pair, `Off!` for off-color cards, `Any` for colorless cards, and `Open` before the ramp starts or before a pair is available. Once locked, pair-filtered 17Lands ratings are used when present with adequate samples; otherwise all-decks ratings remain the fallback.

The TUI pack table shows `17L WR` and `17L Grade` as primary columns. `17L WR` is the raw Games-in-Hand win rate from the resolved 17Lands source. `17L Grade` follows the methodology published on the 17Lands Card Data page for the Grades view: grades are centered at `C` on the selected win-rate metric distribution, and each grade step is a deterministic `0.33` standard-deviation band. Draftgoblin computes those grades from the cached 17Lands GIH distribution for the same source and format/filter context the row uses (QuickDraft for Quick Drafts, PremierDraft when the row is a Premier fallback, or pair-filtered data when used); cards without a strong GIH sample show `—`.

Displayed `DG` scores are whole-number integers. We do not show one decimal for ties because the plain draft table should stay easy to scan; DG ties are resolved deterministically by raw score, base rating, and original pack order.

The TUI is explicit about the active ranking in the title and status bar. Press `s` to cycle between DG Score, 17Lands WR, ALSA, and mana value. When the current recommendation is an early/open pick or the top two cards are very close in the active ranking, the status bar shows confidence copy such as `early/open pick — stay flexible` or `close pick` rather than overclaiming certainty. Backtest reports also print the ranking used before listing recommended-vs-actual picks.

See [benchmarking.md](benchmarking.md) for the offline 17Lands public-data workflow and current calibration evidence. The known non-ML follow-up is reviewing building/locked DG Score misses where trophy drafters still took off-color cards, which may indicate set-specific color-ramp or off-color-penalty tuning.

Rows marked `Prior*` did not have a strong GIH sample. The `Source` column shows whether a row used QuickDraft, Premier fallback, or the prior.
