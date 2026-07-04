# draftgoblin

An unofficial Quick Draft assistant for MTG Arena.

## Status

Draftgoblin is in early scaffold form. The `draftgoblin` CLI entry point exists with parser-backed `replay`, default Textual `watch`, and `watch --plain` commands, score-sorted pick tables, a `refresh-data` command, and a `build` subcommand with pair selection, constrained spells, mana base, and bench output. Draft completion in replay and plain watch automatically prints the build sheet.

See [docs/pick-scoring.md](docs/pick-scoring.md) for the initial 0–100 scoring model and integer tie-display decision. The Textual watch view uses `q` to quit, `c` to toggle secondary columns, and `s` to cycle score/ALSA/mana-value sorting. See [docs/deck-builder.md](docs/deck-builder.md) for deck-builder constraints, 17Lands structure targets, mana-base defaults, relaxation order, and `--allow-splash`.

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
uv run draftgoblin build --pool tests/fixtures/deckbuilder-constrained-pool.json --bulk-file tests/fixtures/deckbuilder-constrained-bulk.jsonl
```

## Disclaimer

Draftgoblin is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC. Card data from 17Lands (17lands.com); 17Lands does not endorse this tool.
