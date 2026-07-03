# Deck builder

The `build` subcommand runs the v1 deck-builder stages that are available in plain CLI mode:

1. Score all two-color pairs from the drafted pool.
2. Select deck spells for the chosen pair under Limited structure defaults.

Stage 3 mana-base output is still deferred.

## Eligible spells

The v1 spell selector considers only cards that are castable in the chosen pair:

- cards whose colors are all in the chosen pair;
- colorless nonland spells, including colorless artifacts;
- in-pair artifacts and other in-pair spells.

Off-pair cards remain excluded. The `--allow-splash` flag is accepted for CLI compatibility, but it is inert in v1 and does not add off-pair cards to the eligible set. Real splash logic is deferred to the later splash ticket.

## Structural defaults

All tunables live in `draftgoblin/config.py` under `DeckBuilderConfig`:

- `target_spell_count = 23`
- `creature_floor = 14`
- `creature_ceiling = 17`
- `minimum_two_drops = 5` at mana value 2
- `maximum_expensive_spells = 3` at mana value 6 or greater
- `near_tie_creature_preference_points = 2.0`

The selector greedily walks score-ranked eligible spells. While the creature floor is unmet, creatures receive the near-tie preference so a creature can beat a noncreature that is within the configured score window.

## Relaxation order

When the pool cannot satisfy every default and still produce the target spell count, constraints are relaxed in this order:

1. expensive-spell cap;
2. minimum two-drop quota;
3. creature ceiling;
4. creature floor;
5. eligible-card shortage.

The build sheet prints both the relaxation order and the relaxations that were actually applied.

