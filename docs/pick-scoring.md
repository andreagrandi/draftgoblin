# Pick scoring

Draft Omen keeps the raw 17Lands GIH win rate visible for every resolved card rating. The Textual watch table ranks by DO Score by default after TMT PremierDraft, TMT TradDraft, and SOS PremierDraft trophy benchmarks showed better top-1/top-3/top-5 match rates and average actual-pick rank than raw 17L WR. It also keeps raw 17L WR visible and switchable for comparison.

The base rating for `DO` is 17Lands GIH WR when the card has enough games-in-hand samples. If QuickDraft data is missing or thin, the resolver falls back to PremierDraft. If neither format has a strong GIH sample, the card uses a neutral prior, adjusted by ALSA when ALSA is available: earlier ALSA raises the prior, later ALSA lowers it.

Scores are normalized against the set rating distribution and centered so the neutral prior displays as 50 before color logic. The five basic lands that can be added freely during deck building instead receive 0 DO points and rank after draftable cards; drafted nonbasic and special lands keep their normal ratings. Color commitment then multiplies the normalized score: on-color cards rise gradually, ordinary off-color cards are penalized gradually, supported splash cards receive a smaller penalty, and colorless cards stay neutral.

Pool color weights come from picked cards. Each colored picked card contributes a quality-weighted amount to each of its colors, so a strong card pulls harder than filler. The highest-weighted two-color pair is the inferred pair once at least two colors have material weight.

During open picks, set/format-specific 17Lands deck color win rates are used as
a close-pick tiebreaker. If cards are within `3.0` DO points and the
hypothetical color-pair weights after taking each card are also close, a
non-generic profile's shrinkage-controlled pair rates must differ by more than
`1pp` before the recommendation prefers the card leading toward the
higher-evidence pair performance. Otherwise, the base comparator retains the
ordering. For a profile-backed pair with observed rate `p`, the tiebreaker
uses `p_prior + w × (p − p_prior)`, where `p_prior` is the neutral pair rate
and `0 ≤ w ≤ 1`. The influence is the product of maturity/confidence, profile
total and per-pair sample support, and aggregate pair-game support. Each
sample factor is `n / (n + k)` and missing evidence contributes zero, so thin
evidence cannot create a material pair-rate margin or overturn base ordering.
This is deliberately disabled once the color ramp starts, so pair win rate
does not override later commitment signals. With no profile or a generic
profile, rates remain raw and the legacy any-nonzero-rate comparison is
preserved, so even a sub-`1pp` difference can resolve a close pick.

Commitment is controlled by documented defaults in `config.py`:

- picks 1-5: open, raw scores (`open_pick_count = 5`)
- pick 6: linear ramp begins (`commitment_start_pick = 6`)
- pick 16+: locked to the inferred pair (`locked_pick_index = 16`)
- locked on-color score multiplier: `1.15`
- locked off-color score multiplier: `0.75`
- pool weight baseline/rating scale/min/max: `1.0`, `10.0`, `0.25`, `2.0`
- open-pick pair-win-rate tiebreaker: within `3.0` DO points and `0.25` pair-weight points
- neutral aggregate pair prior: `neutral_pair_win_rate = 0.5` (independent of the `0.55` card prior)

Rows show a `Fit` marker: `On` for cards inside the inferred pair, `Off!` for off-color cards, `Any` for colorless cards, and `Open` before the ramp starts or before a pair is available. Once locked, pair-filtered 17Lands ratings are used when present with adequate or thin samples. The raw pair rating remains available for `17L WR`, grade, samples, and source metadata; only the score-only base rating is shrunk toward the resolved all-decks card rating. Pair-card influence is bounded by profile maturity/confidence, profile total/per-pair samples, and the pair row's GIH sample count; missing evidence falls back safely toward global evidence rather than inventing certainty.

## Pre-pick scoring context

`build_pick_scoring_context` is the public construction entry point for the
single validated pre-pick context boundary. It accepts the authoritative
pool-before-pick IDs and card database, ratings/config for pair inference, the
exact selected `SetProfile`, and either `pick_index` or the complete stage
coordinates (`pack_number`, `pick_number`, `global_pick_index`,
`estimated_remaining_picks`). Partial coordinates are rejected. An existing
context is authoritative: conflicting coordinates are rejected and the same
context is returned unchanged. `PickEngine.score_pack` resolves stage and
commitment once, then uses the same private validated construction path.
Workflow entry points automatically call `load_scoring_profile` for the active
set/format, selecting a conventional local non-generic profile before
passing it into scoring; an explicitly supplied profile remains authoritative.

`PickScoringContext` is an immutable value with exactly two fields:

- `set_profile: SetProfile`
- `role_ledger: PoolRoleLedger`

Construction validates that both fields have their declared types, the ledger
uses `PRE_PICK_PROJECTION`, its stage is present, and its
`profile_source` is exactly `profile:{set_profile.maturity.value}`. The
validated ledger stage is exposed through the context's read-only `stage`
property.

`ScoredPack.scoring_context` retains the supplied or constructed context
exactly, and `ScoredPack.role_ledger` retains its ledger.

When a validated profile-backed pre-pick context is available, the engine scores
with six small additive contextual terms from that validated pre-pick state:
role need (0–2.5), late urgency (0–3.0), semantic package support (0–1.5),
redundancy (−2.0–0), unsupported payoff (−2.0–0), and fixing need (0–1.5).
Each term is finite and clamped to its documented range. The contextual
contribution is the sum of those six terms clamped to −6.0–+6.0 DO points,
and the raw score is:

`clamp(base score × color factor + contextual contribution, 0, 100)`.

Terms scale with draft stage, profile maturity/confidence, target confidence,
assignment confidence, and existing package evidence, so early picks remain
quality-first. Missing targets are saturated at their preferred minimum; in
particular, late role urgency disappears once that target is met. Empirical
card-pair synergy is not used.

Scored rows and session recommendations retain the immutable term breakdown,
aggregate, and material evidence strings. Recommendation explanations name
the inferred pair and optional theme, profile maturity/confidence, and
material contextual terms without claiming that weak semantic evidence
guarantees an outcome.

`PairProfile.theme` is optional annotation-only metadata. When present, it is
trimmed while preserving the supplied case; it labels a pair theme and does
not independently affect scoring. Contextual scoring uses only the supplied
pre-pick ledger and never projects against offered or future cards. Without a
validated profile-backed context, scores and ordering remain the generic
rating/color results.

All live, recovered, and accountless session paths, replay, backtest, and
benchmark route pre-pick scoring through `PickEngine.score_pack` and use the
`ScoredPack` handoff. Audit serialization consumes that same pack rather than
reconstructing context; watch and TUI adapters consume shared session state
and do not build context.

Session recommendations, replay explanations, and backtest results use the
scored-card contextual evidence. Audit records serialize matching
recommendation/candidate breakdown and evidence, pair/theme/profile metadata,
and context stage/profile provenance, preserving recommendation/audit parity.

Missing, corrupt, incompatible, or generic profiles normalize to no context:
`build_pick_scoring_context` returns `None` and
`ScoredPack.scoring_context` remains `None`; generic rating/color results stay
unchanged, although a stage-aware generic role ledger may still be retained.


## Splash recommendations

Splash recommendations are enabled by default. Open the TUI configuration with `c` to disable them persistently, or start a session with `draftomen-tui watch --no-splash`. `draftomen-tui replay` and `draftomen-tui backtest` also accept `--no-splash`.

The splash policy is deliberately narrower than general three-color drafting:

- Keep exactly one inferred two-color primary pair and consider at most one additional color.
- Take at most two splash cards.
- Require each splash card to have only one mana pip of the splash color and no other color outside the primary pair.
- Require at least an `A-` 17Lands grade and a base DO Score advantage of `5.0` over the best offered on-color card.
- Require three sources for one splash card and four sources for two. At most one source may be a planned basic; the rest must be deterministic drafted fixing lands that are castable in the primary pair.
- Before color lock, an unsupported `A` or `A+` card may be marked `Splash?` as a speculative pick. Speculative splashes are disabled after lock and in aggressive pools.
- Aggressive pools require a supported `A` or better splash.

The `Fit` column uses `Splash X` for a supported splash, `Splash? X` for a speculative one, and `Fix X` when a fixing land directly supports the active splash color. Focused card details show the source count and the exact acceptance or rejection reason. Once a splash color has been established, cards of any other third color remain ordinary off-color cards.

The TUI pack table shows `17L WR` and `17L Grade` as primary columns. `17L WR` is the raw Games-in-Hand win rate from the resolved 17Lands source. `17L Grade` follows the methodology published on the 17Lands Card Data page for the Grades view: grades are centered at `C` on the selected win-rate metric distribution, and each grade step is a deterministic `0.33` standard-deviation band. Draft Omen computes those grades from the cached 17Lands GIH distribution for the same source and format/filter context the row uses (QuickDraft for Quick Drafts, PremierDraft when the row is a Premier fallback, or pair-filtered data when used); cards without a resolved GIH win rate show `—`.

Displayed `DO` scores are whole-number integers. We do not show one decimal for ties because the plain draft table should stay easy to scan; after the open-pick pair-win-rate tiebreaker, remaining DO ties are resolved deterministically by raw score, base rating, and original pack order.

The TUI is explicit about the active ranking in the title and status bar. Press `s` to cycle between DO Score, 17Lands WR, ALSA, and mana value. When the current recommendation is an early/open pick or the top two cards are very close in the active ranking, the status bar shows confidence copy such as `early/open pick — stay flexible` or `close pick` rather than overclaiming certainty. Backtest reports also print the ranking used before listing recommended-vs-actual picks.

See [benchmarking.md](benchmarking.md) for the offline 17Lands public-data workflow and current calibration evidence. The known non-ML follow-up is reviewing building/locked DO Score misses where trophy drafters still took off-color cards, which may indicate set-specific color-ramp or off-color-penalty tuning.

Rows marked `Prior*` did not have a strong GIH sample. The `Source` column shows whether a row used QuickDraft, Premier fallback, or the prior.
