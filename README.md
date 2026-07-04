# draftgoblin

An unofficial Quick Draft assistant for MTG Arena.

## Status

Draftgoblin is in early scaffold form. The `draftgoblin` CLI entry point exists with parser-backed `replay`, default Textual `watch`, and `watch --plain` commands, 17Lands win-rate and grade pick tables, a `refresh-data` command, a `build` subcommand with pair selection, constrained spells, mana base, and bench output, and a `backtest` subcommand that compares saved post-draft recommendations to actual picks. Draft completion in replay and plain watch automatically prints the build sheet.

See [docs/pick-scoring.md](docs/pick-scoring.md) for the 17Lands WR/grade display, the 0–100 Draftgoblin scoring model, and integer tie-display decision. The Textual watch view uses `q` to quit, `c` to toggle secondary columns, `s` to cycle ranking between 17Lands WR (default), DG score, ALSA, and mana value, `b` to open the build view, and `t` to open the post-draft backtest report. See [docs/deck-builder.md](docs/deck-builder.md) for deck-builder constraints, 17Lands structure targets, mana-base defaults, relaxation order, and `--allow-splash`.

Card metadata comes from the cached Scryfall bulk data and is automatically overlaid with MTG Arena's local `data_cards`/`data_loc` files when available, so newly released Arena grpIds can resolve before Scryfall publishes `arena_id` mappings. In Kitty-compatible terminals such as Ghostty, the Textual watch sidebar can show Scryfall image previews for the focused card using image URLs indexed from the local Scryfall bulk cache; run `refresh-data` once after upgrading to populate that image index. Set `DRAFTGOBLIN_CARD_IMAGES=0` to keep the text-only fallback.

## Usage

```bash
uv run draftgoblin --version
uv run draftgoblin
uv run draftgoblin --help
uv run draftgoblin watch --help
uv run draftgoblin refresh-data --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin refresh-structure-targets --set-code VOW --bulk-file path/to/scryfall-default-cards.jsonl --draft-data-file path/to/draft_data_public.VOW.QuickDraft.csv.gz
uv run draftgoblin watch --log-path tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin watch --plain --once --log-path tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin replay tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin backtest --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin build --pool tests/fixtures/deckbuilder-constrained-pool.json --bulk-file tests/fixtures/deckbuilder-constrained-bulk.jsonl
```

## Disclaimer

Draftgoblin is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC. Card data from 17Lands (17lands.com); 17Lands does not endorse this tool.
