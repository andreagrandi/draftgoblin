# Desktop GUI

The PySide6/QML desktop application uses the same immutable session state and
explicit commands as the terminal frontends. A Qt adapter schedules live
session work outside the GUI thread and publishes plain Qt values and item
models to presentation-only QML.

## Launch

Install the optional GUI dependency and launch the live provider:

```bash
uv run --extra gui draftgoblin-gui
```

The live provider follows Arena's standard `Player.log` location and loads the
same card and ratings services as the terminal application. Use `--log-path`
to override the platform default.

Select the deterministic provider for visual development without filesystem or
network dependencies:

```bash
uv run --extra gui draftgoblin-gui --provider mock
```

The legacy `draftgoblin-gui-mockup` command remains an equivalent mock-only
entry point. Use the selectors to open a specific surface, representative
state, or responsive target:

```bash
uv run --extra gui draftgoblin-gui --provider mock --surface build --width 1440 --height 900
uv run --extra gui draftgoblin-gui --provider mock --surface live --width 760 --height 900
uv run --extra gui draftgoblin-gui --provider mock --surface backtest --scenario error
```

The mock-only top-bar selector switches between `loading`, `ready`, `empty`,
`progress`, `warning`, and `error`. Primary navigation opens Live Draft, Deck
Build, and Backtest. Settings remains available from the top bar in both
provider modes.

For an automated launch and render check, use `--smoke-test`. Add
`--screenshot /tmp/draftgoblin-gui.png` to capture the rendered window before
the process exits.

## Responsive behavior

- **Wide, 1440 × 900:** persistent navigation, ranked recommendation workspace, selected-card preview, and pool summary remain side by side. Deck Build keeps the focused-card panel beside the build sections.
- **Narrow, 760 × 900:** persistent desktop navigation becomes compact, recommendation rows stack their secondary facts, and Card details and Pool use an explicit segmented view. Deck Build removes the permanent preview column while retaining summary and rebuild controls.

Resize the running window across the breakpoint to review both arrangements; no restart is required.

## Recorded visual direction

The implementation follows the checked-in **Tactical Grimoire** design system and the selected Stitch references recorded in `gui-design-plan.md`:

- low-glare charcoal and blue-black tonal surfaces;
- goblin green reserved for recommendations, active navigation, and successful status;
- burnt orange for warnings and progress, with warm ivory primary text;
- compact, desktop-native information density with crisp outlines and restrained radii;
- visibly distinct recommended, selected, and keyboard-focus treatments;
- persistent read-only status and 17Lands attribution;
- neutral labelled card-image placeholders rather than generated Magic artwork.

The generated HTML under `ui_mockups/` remains reference material only. QML
owns presentation and local visual formatting. Both providers publish the same
narrow QObject properties and Qt item models, and receive the same explicit
user intentions; parsing, persistence, scoring, builds, backtests, and recovery
remain in shared Python.

