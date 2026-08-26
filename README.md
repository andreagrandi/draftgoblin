<p align="center">
  <img src="https://raw.githubusercontent.com/andreagrandi/draftomen/master/docs/assets/draftomen_logo.png" alt="Draft Omen logo" width="220">
</p>

# Draft Omen

Draft Omen is an unofficial terminal draft assistant for MTG Arena Quick Drafts. It reads Arena's local `Player.log`, recognizes each pack and pick, and ranks the available cards while you draft. When the draft is complete, it also suggests a 40-card deck from your pool.

Draft Omen is read-only: it does not write to, inject into, or automate MTG Arena. Card details come from [Scryfall](https://scryfall.com/) and draft statistics come from [17Lands](https://www.17lands.com/).

## Screenshots

### Live pick recommendations

Cards are ranked in real time, with the raw 17Lands win rate and grade shown alongside Draft Omen's pool-aware score.

![Draft Omen ranking the cards in a Quick Draft pack](https://raw.githubusercontent.com/andreagrandi/draftomen/master/docs/assets/draft-pick-recommendations.png)

### Suggested deck

At the end of the draft, Draft Omen selects a color pair, spells, and mana base for a 40-card build.

![Draft Omen suggested deck build](https://raw.githubusercontent.com/andreagrandi/draftomen/master/docs/assets/suggested-deck-build.png)

## How recommendations work

Draft Omen keeps the data behind every recommendation visible instead of presenting a black-box pick order:

- **17Lands ratings:** each card shows its Games-in-Hand win rate (`17L WR`) and a 17Lands-style grade. Quick Draft data is preferred; Premier Draft data is used as a fallback when Quick Draft samples are missing or too small.
- **DO Score:** the default 0-100 ranking normalizes 17Lands win rates across the set. A card without a reliable sample starts from a neutral score of 50, adjusted by its Average Last Seen At (`ALSA`) when available. The five freely available basic lands instead score 0 and rank after draftable cards.
- **Color fit:** early picks stay open. From pick 6 onward, scores gradually favor the colors supported by the drafted pool; from pick 16, on-color cards receive the full bonus and off-color cards the full penalty. Strong picks influence the inferred color pair more than filler.
- **Close early picks:** when two cards have similar scores, set- and format-specific 17Lands color-pair win rates can break the tie without forcing an early commitment.
- **Conservative splashing:** an `A`-range, single-pip card can be marked as a visible third-color splash when it is materially better than the on-color choices and the pool can support its mana. Draft Omen limits this to one extra color and two cards.
- **Transparent alternatives:** press `s` to compare rankings by DO Score, raw 17Lands win rate, ALSA, or mana value. The interface also identifies neutral-prior and Premier-fallback rows.
- **Deck suggestion:** the builder evaluates two-color pairs using card quality and 17Lands pair performance, then chooses spells and lands while considering creatures, curve, colored mana requirements, and cached 17Lands deck-structure targets when available.

DO Score is the default because it performed better than raw 17Lands win rate in Draft Omen's offline pick benchmarks. It is still guidance rather than a perfect pick order: public statistics reflect the decks, players, and contexts in which cards were played.

For the complete methodology, see [pick scoring](docs/pick-scoring.md), [benchmarking](docs/benchmarking.md), and the [deck builder](docs/deck-builder.md).

## How to use it

### 1. Install

On macOS, install Draft Omen with Homebrew:

```bash
brew install andreagrandi/tap/draftomen
```

On other platforms, [install uv](https://docs.astral.sh/uv/getting-started/installation/) and run:

```bash
uv tool install draftomen
```

### 2. Enable Arena logs

In MTG Arena, open **Settings → Account**, enable **Detailed Logs (Plugin Support)**, and restart Arena.

### 3. Start Draft Omen

Start Draft Omen before entering a Quick Draft:

```bash
draftomen
```

The app loads card metadata when needed, watches Arena's standard log location, detects the set, and follows the draft automatically. If 17Lands ratings are not cached for that set, it offers to download them. Use the arrow keys or `j`/`k` to browse cards, `s` to change the ranking, `b` to open the current build, `c` to configure the view and optional splash recommendations, and `q` to quit.

Live recommendations currently support Quick Draft. Windows support is best-effort.

## Desktop GUI

Install the optional `gui` dependencies and run `draftomen-gui` for the live
PySide6/QML application. The deterministic mock provider remains available for
visual development with `draftomen-gui --provider mock`. See the
[desktop GUI guide](docs/gui-mockup.md) for live and mock launch options,
selectable states, and responsive review targets. For reproducible unsigned
development bundles, see the [desktop bundle guide](docs/desktop-bundles.md).

## Local draft audit data

Live TUI and `watch --plain` sessions keep an independent, append-only JSONL
audit log for every draft under
`~/.draftomen/audit/drafts/<account-id>/<draft-id>.jsonl`. Each record includes
the offered pack, pool snapshot, ratings source, scoring configuration, complete
candidate calculations, splash eligibility and mana-source reasoning, all
supported rankings, the recommendation visible when the pick was made, and the
card actually chosen. Stable record IDs prevent
startup scans and log rotation from rewriting prior evidence.

Offline `replay` and backtest commands do not write audit records. For the schema
and retention details, see [draft audit logging](docs/draft-audit-log.md).

## Branding and compliance

Draft Omen is not affiliated with, sponsored by, approved by, or endorsed by Wizards of the Coast, Scryfall, or 17Lands.

Draft Omen is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC. Card data from Scryfall and 17Lands; neither service endorses this tool.
