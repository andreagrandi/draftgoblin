# Deck builder

The `build` subcommand runs the v1 deck-builder stages that are available in plain CLI mode:

1. Score all two-color pairs from the drafted pool.
2. Select deck spells for the chosen pair under cached empirical targets or Limited fallback defaults.
3. Add a mana base and print a 40-card build sheet with bench cards.

`replay` and `watch --plain` also run these stages automatically when a draft completion event is parsed. Running `draftgoblin` with no subcommand starts watch mode and scans startup logs by default, so a just-finished draft should open directly into the build view.

## Eligible spells

The spell selector always considers cards that are castable in the chosen pair:

- cards whose colors are all in the chosen pair;
- colorless nonland spells, including colorless artifacts;
- in-pair artifacts and other in-pair spells.

Conservative splashing is on by default and can be disabled with `--no-splash` or through the TUI configuration opened with `c`. The builder uses the same shared policy as live picks: one third color, at most two `A-` or better cards, and at most one off-color mana pip per card. One card requires three sources and two cards require four. The builder may plan one basic land of the splash color, so the remaining sources must be deterministic drafted fixing lands usable by the primary pair.

## Structural defaults

All tunables live in `draftgoblin/config.py` under `DeckBuilderConfig`:

- `deck_size = 40`
- `target_spell_count = 23`
- `default_land_count = 17`
- `aggressive_land_count = 16` when average mana value is low and the 2-drop quota is filled
- `top_heavy_land_count = 18` when average mana value is high
- `creature_floor = 14`
- `creature_ceiling = 17`
- `minimum_two_drops = 5` at mana value 2
- `maximum_expensive_spells = 3` at mana value 6 or greater
- `near_tie_creature_preference_points = 2.0`
- `maximum_unresolved_metadata_ratio = 0.25`
- `splash_max_cards = 2`
- `splash_elite_score_minimum = 70.0`
- shared splash grade floor: `A-`
- shared splash-color limit: `1`
- shared source targets: `3` for one card, `4` for two cards
- planned splash basics: at most `1`
- `main_color_source_floor = 7`
- `structure_maindeck_rate_threshold = 0.5`

These are the fallback FR-5.6 structural defaults: established Limited consensus values used when no empirical 17Lands target cache exists for the selected set and pair.

Empirical targets can be computed from 17Lands public draft-data dumps with `refresh-structure-targets`. The command groups trophy drafts by deck, computes average creature count, curve shape, and land count per two-color pair, and caches the small aggregate under the normal 17Lands cache directory. The builder loads those targets automatically in offline `build`, `replay`, and watch flows when the cache is present.

The selector greedily walks score-ranked eligible spells. While the creature floor is unmet, creatures receive the near-tie preference so a creature can beat a noncreature that is within the configured score window.

When the curve calls for 16 or 18 lands, the builder reselects the spell count to keep the final deck exactly 40 cards.

Before pair selection, the builder validates that enough picked cards have card metadata to identify playable spells reliably. If too much metadata is missing, or unresolved cards make the playable count fall below the spell target, the builder refuses to print a deck and asks the user to refresh card data or pass a current bulk file.

## Mana base

Drafted in-pair nonbasic lands are slotted first, up to the land-count target. A nonbasic land is in-pair when its produced mana is known and all produced colors are in the chosen pair. If splash cards are selected, nonbasic lands that produce the one splash color are included and the builder adds at most one basic of that color to meet the shared source target.

Remaining slots are basics. After reserving any required splash basic, the other basics are split proportionally to colored pips in the final spell list, with a 7-source floor for each main color when the available land slots can satisfy it. Ties and leftover basics round toward the color with heavier double-pip requirements.

For 16-land aggressive builds, the output prints the caveat that basics can be preferable to slow taplands when curve pressure matters.

## Build sheet

Plain output includes:

- chosen pair and its 17Lands win rate context;
- exact deck size, spell count, and land count;
- a similarity line when empirical 17Lands structure targets exist;
- spells sorted by curve with creatures and non-creatures separated;
- lands with nonbasics first, then basic counts;
- a bench of the nearest cuts with one-line reasons.

## Relaxation order

When the pool cannot satisfy every default and still produce the target spell count, constraints are relaxed in this order:

1. expensive-spell cap;
2. minimum two-drop quota;
3. creature ceiling;
4. creature floor;
5. eligible-card shortage.

The build sheet prints both the relaxation order and the relaxations that were actually applied.
