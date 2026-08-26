# Desktop GUI

The PySide6/QML desktop application is Draft Omen's default interface. It
uses the same immutable session state and explicit commands as the terminal
frontend. A Qt adapter schedules live session work outside the GUI thread and
publishes plain Qt values and item models to presentation-only QML.

## Launch

Install Draft Omen and launch the live provider:

```bash
uv run draftomen
```

The live provider follows Arena's standard `Player.log` location and loads the
same card and ratings services as the terminal application. Use `--log-path`
to override the platform default.

Select the deterministic provider for an automated smoke check without
filesystem or network dependencies:

```bash
uv run draftomen --provider mock --smoke-test
```

For visual development, use the explicit forced-mock entry point:

```bash
uv run draftomen-gui-mockup
```

Use the selectors to open a specific surface, representative state, or
responsive target:

```bash
uv run draftomen-gui-mockup --surface build --width 1440 --height 900
uv run draftomen-gui-mockup --surface live --width 760 --height 900
uv run draftomen-gui-mockup --surface backtest --scenario error
```

The mock-only top-bar selector switches between `loading`, `ready`, `empty`,
`progress`, `warning`, and `error`. Primary navigation opens Live Draft, Deck
Build, and Backtest. Settings remains available from the top bar in both
provider modes.

To capture a screenshot during the smoke check, add
`--screenshot /tmp/draftomen-smoke.png` before the process exits.

## Responsive behavior

- **Wide, 1440 × 900:** persistent navigation, ranked recommendation workspace, selected-card preview, and pool summary remain side by side. Deck Build keeps the focused-card panel beside the build sections.
- **Narrow, 760 × 900:** persistent desktop navigation becomes compact, recommendation rows stack their secondary facts, and Card details and Pool use an explicit segmented view. Deck Build removes the permanent preview column while retaining summary and rebuild controls.

Resize the running window across the breakpoint to review both arrangements; no restart is required.

## Recorded visual direction

The implementation follows the checked-in **Draft Omen** design system and the selected Stitch references recorded in `gui-design-plan.md`:

- deep midnight-navy tonal surfaces;
- luminous periwinkle reserved for recommendations, active navigation, and successful status;
- champagne gold for warnings and accents, with warm ivory primary text;
- compact, desktop-native information density with crisp outlines and restrained radii;
- visibly distinct recommended, selected, and keyboard-focus treatments;
- persistent read-only status and 17Lands attribution;
- neutral labelled card-image placeholders rather than generated Magic artwork.

The generated HTML under `ui_mockups/` remains reference material only. QML
owns presentation and local visual formatting. Both providers publish the same
narrow QObject properties and Qt item models, and receive the same explicit
user intentions; parsing, persistence, scoring, builds, backtests, and recovery
remain in shared Python.

