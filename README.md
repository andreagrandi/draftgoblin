# draftgoblin

An unofficial Quick Draft assistant for MTG Arena.

## Status

Draftgoblin is in early scaffold form. The `draftgoblin` CLI entry point exists with parser-backed `replay` and `watch --plain` commands, score-sorted pick tables, a `refresh-data` command, and a `build` subcommand with pair selection plus constrained 23-spell selection.

See [docs/pick-scoring.md](docs/pick-scoring.md) for the initial 0–100 scoring model and integer tie-display decision. See [docs/deck-builder.md](docs/deck-builder.md) for deck-builder constraints, relaxation order, and the inert v1 `--allow-splash` flag.

## Usage

```bash
uv run draftgoblin --version
uv run draftgoblin --help
uv run draftgoblin watch --help
uv run draftgoblin refresh-data --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin watch --plain --once --log-path tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin replay tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin build --pool tests/fixtures/deckbuilder-constrained-pool.json --bulk-file tests/fixtures/deckbuilder-constrained-bulk.jsonl
```

## Disclaimer

Draftgoblin is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC. Card data from 17Lands (17lands.com); 17Lands does not endorse this tool.
