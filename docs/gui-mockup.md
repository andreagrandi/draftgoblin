# Desktop GUI mockup

The deterministic PySide6/QML mockup is the visual-development surface for the planned desktop application. It uses production-target QML components and the immutable state and explicit command types from `draftgoblin.session`; it does not read Arena logs, cached card data, or network services.

## Launch

From the repository root, launch the wide live-draft view with:

```bash
uv run --extra gui draftgoblin-gui-mockup
```

The GUI dependency is optional so the terminal application and core modules remain independent of PySide6.

Use the command-line selectors to open a specific surface, representative state, or responsive target:

```bash
uv run --extra gui draftgoblin-gui-mockup --surface build --width 1440 --height 900
uv run --extra gui draftgoblin-gui-mockup --surface live --width 760 --height 900
uv run --extra gui draftgoblin-gui-mockup --surface backtest --scenario error
```

The top-bar state selector switches between `loading`, `ready`, `empty`, `progress`, `warning`, and `error` without filesystem or network dependencies. Primary navigation opens Live Draft, Deck Build, and Backtest. Settings remains available from the top bar.

For an automated launch and render check, use `--smoke-test`. Add `--screenshot /tmp/draftgoblin-gui.png` to capture the rendered window before the process exits.

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

The generated HTML under `ui_mockups/` remains reference material only. QML owns presentation and local visual formatting; deterministic Python publishes representative immutable snapshots and receives the same explicit commands intended for the production adapter.

Production GUI integration must reuse these QML components through a live Qt adapter rather than adding service, persistence, parsing, scoring, build, or backtest behavior to QML.

