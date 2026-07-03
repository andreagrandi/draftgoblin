# draftgoblin

An unofficial Quick Draft assistant for MTG Arena.

## Status

Draftgoblin is in early scaffold form. The `draftgoblin` CLI entry point exists with parser-backed `replay` and `watch --plain` commands, a `refresh-data` command, and a stub `build` subcommand so scoring, deck building, and TUI work can land incrementally.

## Usage

```bash
uv run draftgoblin --version
uv run draftgoblin --help
uv run draftgoblin watch --help
uv run draftgoblin refresh-data --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin watch --plain --once --log-path tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
uv run draftgoblin replay tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
```

## Disclaimer

Draftgoblin is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC. Card data from 17Lands (17lands.com); 17Lands does not endorse this tool.
