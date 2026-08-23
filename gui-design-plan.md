# Draftgoblin GUI design plan for Google Stitch

## Purpose

Create reference mockups for Draftgoblin's planned PySide6 and QML desktop application. The mockups should establish the visual language, information hierarchy, responsive behavior, and principal interaction states before production GUI integration starts.

These designs are references, not an implementation contract. Preserve the product behavior and state described here, but leave exact spacing, type scale, colors, and component polish open to iteration after the mockups are reviewed.

## How to use this plan in Stitch

1. Upload these repository assets with this document:
   - `docs/assets/draftgoblin_logo.png`
   - `docs/assets/draft-pick-recommendations.png`
   - `docs/assets/suggested-deck-build.png`
2. Give Stitch the master prompt below.
3. Start with two visual variations of **DG-01 — Live draft, wide, ready**. Select and refine one direction before expanding it.
4. Ask Stitch to generate the remaining named frames one group at a time, always reusing the selected component library and design tokens.
5. Export both the wide and narrow variants. Do not approve a direction based on the wide desktop frame alone.
6. Treat the existing TUI screenshots as information references only. Do not reproduce the terminal layout literally.

## Exported Stitch references

The selected Stitch exports are checked in under `ui_mockups/stitch_draftgoblin_companion/`:

- `tactical_grimoire/DESIGN.md` defines the shared visual system and design tokens.
- `dg_01_refined_direction_hybrid/` contains the selected live-draft direction and its HTML reference.
- `dg_06_deck_build_wide/` contains the wide deck-build reference and a complete PNG export.
- `dg_07_deck_build_narrow/` contains the narrow deck-build reference and its HTML reference.

These exports are visual references only. The issue acceptance criteria, shared session contract, and UI architecture skill remain authoritative for behavior. Do not copy the generated HTML into the QML application or infer unsupported application behavior from the mock data.

The current Stitch drop has two screenshot-export limitations: the DG-01 `screen.png` contains an image-fetch failure placeholder, and the DG-07 `screen.png` is truncated to a header strip. Use their complete `code.html` files as the source references until replacement PNGs are exported.

## Master prompt for Stitch

Design a polished, modern desktop companion app for **Draftgoblin**, an unofficial, read-only Quick Draft assistant for MTG Arena. Draftgoblin watches Arena's local log, ranks the cards currently offered, explains the evidence behind its recommendation, tracks the drafted pool, and suggests a 40-card deck when the draft is complete.

Create a cohesive dark-theme desktop product using the supplied Draftgoblin logo as the brand reference. The experience should feel clever, focused, trustworthy, and slightly mischievous, but not childish. Translate the information in the supplied terminal screenshots into a genuinely graphical hierarchy; do not imitate a terminal, MTG Arena, or another tracker product.

The most important user question during a live draft is: **"Which card is recommended, and why?"** Make the top recommendation, its Draftgoblin score, raw 17Lands win rate, grade, color fit, and supporting card details easy to understand at a glance. Secondary statistics remain available without overwhelming the main decision.

Draftgoblin is advisory and read-only. Selecting a row only changes the focused details. Never show a button that claims to pick a card in Arena, modify Arena, or automate gameplay. Use honest state copy such as "Watching Arena", "Waiting for the next pack", and "Recommendation updated".

Produce the named wide and narrow frames below, plus a compact component/state sheet. Use reusable components, consistent tokens, strong keyboard focus treatment, accessible contrast, scalable layouts, and restrained motion. All screens should look feasible to implement with Qt Quick Controls and QML.

## Product principles

- **Recommendation first.** Live drafting is time-sensitive. The suggested card and the reason for the suggestion dominate the view.
- **Evidence, not authority.** Keep raw 17Lands data visible alongside Draftgoblin's pool-aware score. Confidence language should acknowledge close or early picks rather than overclaim certainty.
- **Read-only companion.** The app observes Arena and presents guidance. The player always makes the actual pick in Arena.
- **State is always clear.** The user should know whether the app is loading, waiting for Arena, following an active draft, downloading ratings, building a deck, or showing an error.
- **Progressive disclosure.** Essential facts stay visible; dense statistics, pool detail, and methodology are available without turning the main screen into a spreadsheet.
- **Desktop-native and responsive.** Design for keyboard and pointer use at wide and narrow desktop window sizes. This is not a mobile app.
- **Accessible by construction.** Color supplements text and icons; it never carries meaning alone.

## Architecture guardrails for later implementation

- Python remains authoritative for parsing, persistence, networking, scoring, ratings, recovery, builds, and backtests.
- QML will render typed, immutable state and emit explicit user intentions. It must not read files, call services, calculate recommendations, mutate the pool, or parse build and backtest text.
- The Qt adapter will translate shared state into Qt properties, signals, and item models. The design must not require QML to access Python domain objects directly.
- If a mockup introduces a useful derived value that is not yet in the shared view state, such as a recommendation explanation, letter grade, color distribution, or mana curve, add it later as a typed Python-owned presentation value. Do not recreate the calculation in QML.
- Reuse the same visual components with deterministic mock data first, then connect them to the production adapter after the direction is reviewed.

## Visual direction

### Character

Use a dark, low-glare workspace with a tactile fantasy-tool character. The visual impression should be closer to a well-made cartographer's or deck builder's workbench than a game HUD. Keep surfaces crisp and contemporary so dense data remains calm and readable.

### Brand cues

- Derive the primary accent from the goblin green in the supplied logo.
- Use parchment or warm ivory for high-emphasis text and a restrained burnt-orange accent for progress, warnings, and small brand details.
- Use charcoal and blue-black surfaces with subtle tonal separation rather than heavy outlines everywhere.
- Reserve the five mana colors for card-color semantics. Do not reuse them as generic navigation or status colors.
- The full logo is suitable for onboarding, empty states, and About. Use a simplified goblin mark or wordmark in the persistent app shell so branding does not consume working space.
- Avoid faux-stone panels, excessive medieval ornament, neon gamer effects, glossy gradients, and constant animation.

### Typography and density

- Use a readable modern UI sans serif for navigation and prose.
- A tabular or monospaced numeral style may be used for scores, percentages, and pick coordinates, but the app should not look terminal-based.
- Support comfortable and compact density. Default to comfortable rows with at least a 40-pixel target height.
- Use short labels and align numeric columns consistently.

### Card imagery

- Treat card images as content supplied by Draftgoblin, not as background decoration.
- Preserve the card's aspect ratio and never crop rules text in the focused preview.
- For generated mockups, use neutral framed placeholders labelled with the card name unless supplied reference art is available. Do not synthesize new MTG card art or recreate the Arena interface.

## Global application shell

Use one persistent shell across the main surfaces:

- A compact top app bar with the Draftgoblin mark, current application phase, Arena account selector, and a settings action.
- Primary navigation for **Live Draft**, **Deck Build**, and **Backtest**. Settings may be a top-bar action rather than a fourth primary destination.
- A persistent but unobtrusive source/status area that can show the active set, ratings source, and "Data from 17Lands" attribution.
- Toasts are only for brief confirmations. Loading, download progress, and recoverable errors must also have a durable location in the relevant screen.
- The shell must work with keyboard navigation. Show a clear focus ring that is distinct from selection and recommendation styling.

The visual treatment must distinguish these concepts:

- **Recommended:** the top-ranked card according to the active ranking.
- **Selected/focused:** the row whose details are open.
- **Keyboard focus:** the control that will receive the next keyboard action.
- **Color fit:** on-color, open, colorless, off-color, or a supported/speculative splash.

## Responsive targets

Design each principal surface at both targets:

- **Wide desktop:** 1440 x 900 logical pixels. Use a multi-column workspace with the recommendation list as the largest region and contextual detail beside it.
- **Narrow desktop:** 760 x 900 logical pixels. Preserve full functionality without horizontal scrolling. Stack regions, collapse secondary columns into row detail, and use tabs or segmented controls for **Card details** and **Pool**.

At the narrow target:

- Keep the current pack/pick, recommendation, active ranking, and live status visible near the top.
- Replace a dense table with stacked recommendation rows or cards.
- Keep the focused card image available without forcing it above the full recommendation list.
- Do not turn persistent desktop navigation into a mobile bottom tab bar.
- Do not hide actions behind hover-only affordances.

## Representative content

Use realistic data so the layout is tested against the actual product shape:

- Account: `MagoAnubiTest`
- Set: `OTJ — Outlaws of Thunder Junction`
- Event: `Quick Draft`
- Draft position: `Pack 1 · Pick 1`
- Pool: `0 / 42 cards`
- Inferred pair: `Open`
- Active ranking: `DG Score`
- Confidence: `Early/open pick — stay flexible`
- Ratings source: `Quick Draft`

Example recommendation rows:

| Rank | Card | Colors | DG | 17L WR | Grade | Fit | ALSA | MV | Source |
| --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- |
| 1 | Outcaster Trailblazer | G | 99 | 63.6% | A- | Open | 1.30 | 3 | Quick |
| 2 | Beastbond Outcaster | G | 87 | 61.6% | B+ | Open | 3.87 | 3 | Quick |
| 3 | Freestrider Lookout | G | 69 | 58.4% | C+ | Open | 1.23 | 3 | Quick |
| 4 | Tumbleweed Rising | G | 68 | 58.2% | C+ | Open | 6.27 | 2 | Quick |
| 5 | Drover Grizzly | G | 67 | 57.9% | C+ | Open | 7.09 | 3 | Quick |
| 6 | Mourner's Surprise | B | 65 | 57.7% | C+ | Open | 6.33 | 2 | Quick |
| 7 | Jailbreak Scheme | U | 62 | 57.2% | C | Open | 5.37 | 1 | Quick |
| 8 | Jagged Barrens | Colorless | 62 | 57.1% | C | Any | 5.48 | 0 | Quick |

Long names, duplicate quantities, missing ratings, multicolor cards, and `6+` mana values should all fit without clipping.

## Screen and frame brief

### DG-01 — Live draft, wide, ready

Create the primary 1440 x 900 frame.

The top of the workspace should show:

- `Pack 1 · Pick 1`
- `14 cards available`
- Ranking selector: **DG Score**, **17L WR**, **ALSA**, **Mana value**
- Confidence copy: `Early/open pick — stay flexible`
- Passive live state: `Watching Arena · Waiting for your pick`

The main workspace should contain:

1. **Recommendation list** as the dominant region.
   - Make rank, card name, DG score, 17L WR, grade, and color fit immediately scannable.
   - Allow ALSA, mana value, and source to be visually subordinate or shown in an expanded details mode.
   - Give rank 1 a clear `Recommended` treatment without making lower rows look disabled.
   - Selecting or focusing a row updates the card detail panel only.
2. **Focused card panel** with full card image, name, colors, type, mana value, DG score, 17L WR, grade, ALSA, fit, and source.
   - Add a brief explanation block such as `Highest pool-aware score; colors are still open.`
   - This explanation is descriptive, not a guarantee.
3. **Pool summary** with pool count, inferred color pair, commitment, color distribution, mana curve, and recent picks.
   - For P1P1, render an intentional empty state rather than empty charts: `Your pool will appear after the first pick.`

The current account, active set, data source, and 17Lands attribution must be available without competing with the recommendation.

### DG-02 — Live draft, narrow, ready

Create the 760 x 900 version of DG-01.

- Use vertical recommendation rows rather than a horizontally compressed table.
- Each row should keep rank, card name, colors, DG score, 17L WR, grade, and fit visible.
- Put ALSA, mana value, and source in an expandable secondary line or the details pane.
- Use a compact selector for ranking modes.
- Place **Card details** and **Pool** behind an obvious two-option segmented control beneath or beside the list. Do not make either destination hover-only.
- Demonstrate a focused row and a visible card preview without obscuring the pack list.

### DG-03 — Waiting and readiness, wide

Design the state before an active pack is available. This should feel useful, not like a blank loading screen.

Show a readiness checklist or status stack for:

- Card metadata: `Ready`
- Arena log: `Watching Player.log`
- Arena account: `MagoAnubiTest`
- Draft: `Waiting for a Quick Draft`
- Ratings: `Set will be detected when the draft starts`

Use concise supporting copy: `Start Draftgoblin before entering a Quick Draft. The app follows Arena automatically and never writes to the game.`

Include a small brand illustration or logo treatment, but keep the operational state more prominent than marketing copy.

Also design variants for:

- Initial card metadata loading, with indeterminate progress.
- No Arena account detected yet.
- A recovered in-progress draft, clearly explaining that Draftgoblin resumed it automatically.
- Multiple known Arena accounts, using the account selector without exposing raw identifiers as the primary label.

### DG-04 — Ratings missing and download progress

Create two related state variants, which may be presented as a modal and an inline progress panel.

**Missing ratings confirmation**

- Title: `Download ratings for OTJ?`
- Explain that Draftgoblin is temporarily using neutral-prior scores.
- Explain that Quick Draft ratings and Premier fallback data will be cached.
- Primary action: **Download ratings**
- Secondary action: **Not now**
- Avoid alarm styling; the draft can continue without the download.

**Download in progress**

- Title: `Downloading OTJ ratings`
- Show determinate progress when counts are available and an indeterminate state otherwise.
- Explain: `The current pack will be rescored automatically when data is ready.`
- The rest of the app should remain readable. Do not use a blocking full-screen spinner.

### DG-05 — Live draft, building colors and close pick

Create a wide variant later in a draft to exercise states that P1P1 does not show:

- Position: `Pack 2 · Pick 4`
- Pool: `18 / 42 cards`
- Inferred pair: `White · Green`
- Commitment: `64% building`
- Confidence: `Close pick`
- Include on-color, off-color, colorless, and `Splash?` row treatments.
- Show a populated pool distribution, mana curve, and recent picks.
- Make the top two recommendations visually close while still preserving their exact ranks.
- Add a short explanation of why the recommendation fits the current pool.

### DG-06 — Deck build, wide

Create a 1440 x 900 completed-draft build screen.

Use the representative summary:

- Pair: `White · Green (automatic)`
- Deck: `40 cards · 23 spells · 17 lands`
- Average mana value: `2.96`
- Creatures: `14`
- Noncreatures: `9`

The hierarchy should be:

1. Deck summary and validation state.
2. Selected spell curve or grouped spell list.
3. Mana base.
4. Bench and detailed pair reasoning.

Include:

- A color-pair selector that clearly distinguishes the automatic pair from a user override.
- A **Rebuild** action that sends the selected pair and splash preference as an explicit request.
- A mana-curve visualization with accessible labels.
- Spell groups by mana value with quantities, card colors, grades, and scores.
- Land summary: `7 Plains · 10 Forests`, with room for drafted nonbasic lands.
- Bench cards in a separate, clearly labelled region.
- Warnings or relaxed constraints as durable, readable notices.
- Focused-card details using the same card component as the live draft screen.

Do not imply that the deck can be imported into Arena. Use copy such as `Suggested deck — recreate this build in Arena.`

### DG-07 — Deck build, narrow

Create the 760 x 900 version of DG-06.

- Keep deck size, pair, validation, and rebuild controls visible before the long card list.
- Use collapsible sections for **Spells**, **Lands**, **Bench**, and **Why this pair**.
- Keep the mana curve horizontally readable and labelled.
- Demonstrate how focused card details open without permanently consuming half the narrow window.

### DG-08 — Backtest report

Create a desktop report for comparing persisted picks with what the active ranking would recommend.

Show:

- Draft identity and set.
- Ranking mode used.
- Summary values for compared, matched, and skipped picks.
- A table or list with pack/pick, recommended card, actual card, match state, recommendation score, win rate, and data source.
- Clear treatment for skipped rows with a human-readable reason.
- A **Run backtest** or **Run again** action.

This is an analytical comparison, not a player grade. Avoid celebratory or punitive scoring language.

Also provide empty and failure variants:

- `Complete or recover a draft to run a backtest.`
- `Some pick history is missing; available picks can still be compared.`
- Recoverable error with **Retry** and **Dismiss** actions.

### DG-09 — Settings

Create a focused desktop settings surface with clear groups and concise explanations.

**Draft guidance**

- Default ranking: DG Score, 17L WR, ALSA, or mana value.
- Splash recommendations toggle with explanatory copy.

**Display**

- Comfortable or compact density.
- Show secondary statistics.
- Show card image preview.
- Show detailed build context.

**Accessibility**

- Respect system text scaling and reduced-motion preferences.
- Do not invent a custom control when the app simply follows the system setting; communicate the detected behavior instead.

**Data status**

- Card metadata state.
- Ratings cache state for the active set.
- Explicit **Download or refresh ratings** action.
- 17Lands attribution.

Use autosave only when the saved state is clear. Otherwise provide **Save** and **Cancel**. Do not expose filesystem mutation, network implementation details, or terminal-only preferences in the UI.

### DG-10 — Recoverable errors and empty states sheet

Create a compact state sheet demonstrating consistent visual and copy patterns for:

- Arena log unavailable.
- Card metadata failed to load.
- Ratings download failed while neutral-prior scoring remains available.
- Card image unavailable.
- No active draft.
- No picked cards yet.
- Build unavailable because card metadata is incomplete.
- Backtest history incomplete.

Each error must say what happened, what still works, and what the user can do next. Only show **Retry** when retry is actually available. Include **Dismiss** for recoverable, non-blocking errors. Never present a technical exception or raw filesystem path as the main user message.

## Interaction annotations

The visual prototype should communicate these behaviors:

| User action | Visible result |
| --- | --- |
| Select a recommendation | Focused card details update; no Arena action occurs. |
| Change ranking | Rows reorder and the recommended treatment moves to rank 1. |
| Toggle splash recommendations | The pack can be rescored; the active preference is visible. |
| Choose an Arena account | The active persisted draft and pool update after confirmation from the app state. |
| Request ratings download | Inline progress appears; the current pack is automatically rescored when ready. |
| Request build or pair override | Progress appears, followed by a new structured build or a recoverable error. |
| Request backtest | Progress appears, followed by a structured comparison report. |
| Retry or dismiss an error | The app publishes a new state; the UI does not mutate an error list locally. |

Avoid speculative actions outside the planned product, including picking a card in Arena, dragging cards into a deck, editing card scores, social sharing, user accounts for Draftgoblin, cloud sync, marketplace features, or AI chat.

## Accessibility requirements

- Meet WCAG AA contrast for text and controls in the default theme.
- Provide visible focus for every interactive element and a logical focus order.
- Never rely on green/red or mana colors alone; pair them with labels, shapes, or icons.
- Use accessible names for card rows, image previews, ranking controls, progress, charts, and icon-only buttons.
- Make score changes understandable without animation.
- Use reduced motion when requested by the system.
- Ensure layouts survive larger text without clipping important data or actions.
- Keep recommendation, selection, and focus treatments visually distinct.

## Component and state sheet

Ask Stitch to include reusable examples of:

- App bar and primary navigation.
- Application phase/status indicator.
- Arena account selector.
- Ranking selector.
- Recommendation row in default, recommended, selected, focused, no-data, off-color, and splash states.
- Focused card panel with loaded, loading, and unavailable image states.
- Pool summary, color distribution, and mana curve.
- Progress panel in determinate and indeterminate states.
- Inline warning, blocking error, and recoverable error.
- Empty state.
- Primary, secondary, quiet, and destructive button styles. Destructive styling should be rare.
- Keyboard focus ring.
- Tooltip and accessible chart label treatment.

## Output request

Deliver:

1. Two initial DG-01 visual variations for direction selection.
2. After one direction is selected, all ten named frame groups with consistent wide and narrow variants where requested.
3. One shared component/state sheet.
4. A small token sheet covering colors, typography, spacing, corner radii, elevation, focus, and motion.
5. Short annotations for responsive behavior and non-obvious interactions.
6. Export-ready mockups suitable for later use as visual references during QML implementation.

Do not provide application code yet. The purpose of this Stitch pass is to review and record the visual direction before Draftgoblin's production GUI is connected to live services.
