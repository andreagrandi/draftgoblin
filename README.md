<p align="center">
  <img src="https://raw.githubusercontent.com/andreagrandi/draftgoblin/master/docs/assets/draftgoblin_logo.png" alt="Draftgoblin logo" width="220">
</p>

# Draftgoblin

Draftgoblin is an unofficial terminal Quick Draft assistant for MTG Arena.

It watches MTG Arena's local `Player.log`, identifies each Quick Draft pack and pick from logged numeric card IDs, and shows score-ranked recommendations using cached Scryfall metadata and 17Lands data. Draftgoblin is read-only: it does not write to, inject into, or automate the Arena client. It does not use OCR, screen capture, overlays, or accessibility permissions.

## Status

Draftgoblin is in early scaffold form. The `draftgoblin` CLI entry point exists with parser-backed `replay`, default Textual `watch`, and `watch --plain` commands, 17Lands win-rate and grade pick tables, a `refresh-data` command, a `refresh-structure-targets` command, a `build` subcommand with pair selection, constrained spells, mana base, and bench output, a `backtest` subcommand that compares saved post-draft recommendations to actual picks, and a `benchmark-picks` subcommand for offline 17Lands public-data calibration. Draft completion in replay and plain watch automatically prints the build sheet.

See [pick scoring](https://github.com/andreagrandi/draftgoblin/blob/master/docs/pick-scoring.md) for the 17Lands WR/grade display, the 0-100 Draftgoblin scoring model, and integer tie-display decision. See [benchmarking](https://github.com/andreagrandi/draftgoblin/blob/master/docs/benchmarking.md) for the offline 17Lands public-data workflow used to compare raw 17L WR against DG Score, and [ML pick recommendations](https://github.com/andreagrandi/draftgoblin/blob/master/docs/ml-pick-recommendations.md) for the future ML research design, data constraints, evaluation gate, and integration boundary. The Textual watch view uses `q` to quit, `c` to configure persisted optional elements, `s` to cycle ranking between DG Score (default), 17L WR, ALSA, and mana value, `b` to open the build view, `t` to open the post-draft backtest report, and `m` to toggle opt-in Mana font icons. See the [deck builder documentation](https://github.com/andreagrandi/draftgoblin/blob/master/docs/deck-builder.md) for deck-builder constraints, 17Lands structure targets, mana-base defaults, relaxation order, and `--allow-splash`.

Card metadata comes from the cached Scryfall bulk data and is automatically overlaid with MTG Arena's local `data_cards`/`data_loc` files when available, so newly released Arena grpIds can resolve before Scryfall publishes `arena_id` mappings. In Kitty-compatible terminals such as Ghostty, the Textual watch sidebar can show Scryfall image previews for the focused card using image URLs indexed from the local Scryfall bulk cache; run `refresh-data` once after upgrading to populate that image index. Set `DRAFTGOBLIN_CARD_IMAGES=0` to keep the text-only fallback.

## Setup

### 1. Install

Draftgoblin requires [uv](https://docs.astral.sh/uv/), which manages its required Python 3.12+ runtime and dependencies in an isolated environment.

Install uv with Homebrew on macOS:

```bash
brew install uv
```

On Windows, install uv with WinGet:

```powershell
winget install --id=astral-sh.uv -e
```

Then install Draftgoblin:

```bash
uv tool install draftgoblin
draftgoblin --help
```

Upgrade or remove the installed command with `uv tool upgrade draftgoblin` or `uv tool uninstall draftgoblin`.

### 2. Enable MTG Arena detailed logs

Draftgoblin can only see draft events after Arena writes plugin logs.

1. Open MTG Arena.
2. Go to **Settings** -> **Account**.
3. Enable **Detailed Logs (Plugin Support)**.
4. Restart MTG Arena so the setting takes effect.
5. Start a Quick Draft, then run `draftgoblin watch`.

The tool reads the current OS user's default `Player.log`. Use `--log-path` if Arena writes logs somewhere else.

### 3. Refresh card metadata

Run this once before live use, and again when you want to update cached card metadata:

```bash
draftgoblin refresh-data
```

For repeatable local development or offline tests, pass a local Scryfall bulk file:

```bash
draftgoblin refresh-data --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
```

## Development

From a source checkout:

```bash
git clone https://github.com/andreagrandi/draftgoblin.git
cd draftgoblin
uv sync
uv run draftgoblin --help
```

Run the full local verification suite with:

```bash
uv run nox -s ci
```

This is the same command GitHub Actions runs.

Maintainers can follow the [release instructions](https://github.com/andreagrandi/draftgoblin/blob/master/docs/releasing.md) to publish a tagged version to PyPI.

## Platform support

| OS | Default `Player.log` location | Support level |
|---|---|---|
| macOS | `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log` | Primary — developed and tested here |
| Windows | `%USERPROFILE%\AppData\LocalLow\Wizards Of The Coast\MTGA\Player.log` | Best-effort — path resolution implemented, untested until tried |
| Linux (Wine/Proton) | inside the Wine prefix (varies) | Unsupported — works via `--log-path` override only |

Both defaults derive from the running OS user's home directory, so separate OS user accounts naturally read separate Arena logs.

## Command reference

These examples use the installed `draftgoblin` command. From a source checkout, prefix the commands with `uv run`.

### `draftgoblin --version`

Print the installed version and required Fan Content disclaimer.

```bash
draftgoblin --version
```

### `draftgoblin watch`

Watch `Player.log` and show live draft recommendations. The default view is a Textual TUI; `--plain` streams replay-compatible text.

When the TUI sees a set with no local 17Lands cache, it warns that neutral-prior scores are active and offers to download the Quick Draft and Premier fallback data. Downloads use 17Lands' all-time period, so returning sets retain their historical samples. The TUI shows request progress, reports how many cards in the current pack have usable ratings, and recalculates the pack as soon as the data is ready. Press `d` to reopen the offer after choosing **Not now**. Existing caches continue to refresh on their normal daily cadence, and legacy date-range caches are replaced automatically.

```bash
draftgoblin watch
draftgoblin watch --mana-icons
draftgoblin watch --log-path ~/Library/Logs/Wizards\ Of\ The\ Coast/MTGA/Player.log
draftgoblin watch --plain --once --log-path tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
```

### `draftgoblin replay`

Replay a captured `Player.log` fixture in deterministic plain-text mode.

```bash
draftgoblin replay tests/fixtures/quick-draft-msh-player.log --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
```

### `draftgoblin build`

Build a 40-card deck from a persisted draft pool or a JSON pool file. The builder selects a color pair, spells, lands, and the nearest bench cuts; `--pair` forces a two-color pair and `--allow-splash` enables the splash heuristic.

```bash
draftgoblin build --pool tests/fixtures/deckbuilder-constrained-pool.json --bulk-file tests/fixtures/deckbuilder-constrained-bulk.jsonl
draftgoblin build --account example-account --draft-id example-draft --pair WU
draftgoblin build --pool pool.json --set-code TDM --allow-splash
```

### `draftgoblin backtest`

Replay saved pick history from a persisted draft and compare current recommendations to the actual picks.

```bash
draftgoblin backtest --bulk-file tests/fixtures/scryfall-default-cards-sample.jsonl
draftgoblin backtest --account example-account --draft-id example-draft --ranking win_rate
```

### `draftgoblin benchmark-picks`

Benchmark pick rankings against a local 17Lands public draft-data CSV or archive.

```bash
draftgoblin benchmark-picks --set-code TMT --format PremierDraft --draft-data-file path/to/draft_data_public.TMT.PremierDraft.csv.gz
draftgoblin benchmark-picks --set-code TMT --draft-data-file path/to/draft_data_public.TMT.PremierDraft.csv.gz --max-drafts 100 --include-non-trophy
```

### `draftgoblin refresh-data`

Refresh cached Scryfall card metadata and overlay local MTG Arena card data when available.

```bash
draftgoblin refresh-data
draftgoblin refresh-data --bulk-file path/to/scryfall-default-cards.jsonl.gz
```

### `draftgoblin refresh-structure-targets`

Compute cached per-pair deck-structure targets from a local 17Lands public draft-data dump.

```bash
draftgoblin refresh-structure-targets --set-code VOW --draft-data-file path/to/draft_data_public.VOW.QuickDraft.csv.gz --bulk-file path/to/scryfall-default-cards.jsonl
```

## Known limitations

- Live recommendations target Quick Draft. Premier Draft, Traditional Draft, Sealed, Cube, and other formats are out of scope for live mode.
- Day-one or otherwise thin formats may have a valid downloaded 17Lands dataset without enough game-in-hand samples for every card. The TUI reports usable coverage after download; cards without a strong sample keep their visible neutral-prior fallback until upstream data matures.
- GIH WR is biased by deck quality, player choices, and card context. DG Score normalizes and adds color-commitment logic, but it is still guidance, not a perfect pick order.
- The deck builder prints a build sheet for manual use in Arena. It does not import decks or automate game input.
- Windows support is best-effort until tested on real installations. Linux is unsupported except by pointing `--log-path` at a Wine or Proton log.
- 17Lands endpoint shapes are not guaranteed stable; cached data keeps the tool useful offline, but fetch code may need updates after upstream changes.

## Optional Mana font icons

The TUI uses plain `W`, `U`, `B`, `R`, `G`, and `Colorless` text by default. Mana icons are explicitly opt-in because terminals without Andrew Gioia's Mana font show missing-glyph boxes.

To enable icons, start the TUI with `--mana-icons` or press `m` inside the TUI. Install and configure the Mana font before enabling it:

1. On macOS, download the latest Mana font zip from <https://github.com/andrewgioia/mana/archive/master.zip>.
2. Unzip it, open `mana-master/fonts/mana.ttf` in Font Book, and click **Install**.
3. In Ghostty, map Draftgoblin's Mana private-use glyphs to the Mana font:

   ```ini
   font-codepoint-map = U+E600-U+E604,U+E61E-U+E624,U+E904=Mana
   ```

   Reload Ghostty or restart it after changing the config.

## TUI configuration

Press `c` in the Textual watch view to open the **TUI config** dialog. It describes every option and offers **Save**, **Cancel**, and **Reset defaults** actions. Saved choices persist in `~/.draftgoblin/tui-preferences.json` (or `<app-dir>/tui-preferences.json` when using the development-only `--app-dir` override). The file is versioned JSON and may also be edited manually while Draftgoblin is not running.

| Setting | Default | Affects |
| --- | --- | --- |
| Secondary pack columns | On | Shows Fit, ALSA, mana value, and source columns when the terminal is wide enough. |
| Build details | Off | Shows build context, picked pool, pair reasoning, structure checks, and bench cuts. |
| Pool metadata | On | Shows the set, event, pool size, pair information, build status, and card-metadata status. |
| Pool color distribution | On | Shows the sidebar color bar. |
| Mana curve | On | Shows pool and detailed-build mana curves. |
| Account identifier | On | Shows the active account in the status bar and build context. |
| Draft identifier | On | Shows draft IDs in pool and build metadata. |
| Mana pips and sources | On | Shows detailed-build mana requirements and source counts. |
| 17Lands attribution | On | Shows the card-data attribution in the status bar. Project disclaimers remain in this README. |
| Focused card details | On | Shows highlighted-card statistics in the sidebar. |
| Card image preview | Auto | `Auto` follows terminal detection; `Show` requests previews where `textual-image` can render them; `Hide` prevents preview loading. |

Responsive layout remains authoritative: a narrow terminal can temporarily hide the sidebar or secondary columns without changing the saved preference. The `DRAFTGOBLIN_CARD_IMAGES` environment variable still controls image detection in `Auto` mode. These settings apply only to `draftgoblin watch`'s Textual TUI; `watch --plain`, `replay`, and `build` keep their existing output.

## Branding and compliance

Draftgoblin uses Wizards of the Coast and 17Lands names only for descriptive attribution. The project is not affiliated with, sponsored by, approved by, or endorsed by Wizards of the Coast or 17Lands.

Draftgoblin is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC. Card data from 17Lands (17lands.com); 17Lands does not endorse this tool.
