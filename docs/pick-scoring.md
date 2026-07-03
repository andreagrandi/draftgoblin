# Pick scoring

Draftgoblin turns each offered card into one integer score from 0 to 100.

The base rating is 17Lands GIH WR when the card has enough games-in-hand samples. If QuickDraft data is missing or thin, the resolver falls back to PremierDraft. If neither format has a strong GIH sample, the card uses a neutral prior, adjusted by ALSA when ALSA is available: earlier ALSA raises the prior, later ALSA lowers it.

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

Displayed scores are whole-number integers. We do not show one decimal for ties because the plain draft table should stay easy to scan; ties are resolved deterministically by raw score, base rating, and original pack order.

Rows marked `Prior*` did not have a strong GIH sample. The `Source` column shows whether a row used QuickDraft, Premier fallback, or the prior.
