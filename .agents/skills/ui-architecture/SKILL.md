---
name: ui-architecture
description: >-
  Preserve Draft Omen's UI-neutral application boundary when changing the
  shared live session, TUI or plain-watch adapters, PySide6 adapter, QML,
  frontend tests, or desktop packaging.
---

# Draft Omen UI Architecture

Keep domain behavior in shared Python and make every frontend an adapter over
the same application contract. The canonical immutable state and command types
live in `draftomen.session`.

Use this dependency direction:

```text
domain services -> live session -> frontend adapters -> presentation
```

Never introduce a dependency in the opposite direction.

## Enforceable rules

1. Python is authoritative for parsing, persistence, networking, scoring,
   auditing, recovery, builds, and backtests.
2. QML is presentation-only. It renders published state and emits user
   intentions; it does not own application or domain behavior.
3. JavaScript in QML is limited to local formatting, property bindings, and
   visual calculations.
4. PySide6 is isolated behind a narrow Qt adapter that translates immutable
   Python state into Qt properties, signals, and item models.
5. Python publishes immutable `LiveSessionSnapshot` values, and frontends send
   explicit `LiveSessionCommand` intentions instead of mutating domain state.
6. Core and application modules never import Textual, Rich, PySide6, or
   QML-specific types. Framework imports belong only in their frontend adapter.
7. QML is divided into focused, reusable components rather than a monolithic
   root file.
8. Core and application behavior, Qt adapter translation, QML interactions,
   and packaged user workflows each have tests at the appropriate boundary.
9. QML modules, Qt plugins, resources, fonts, icons, and other deployment inputs
   are declared explicitly and verified in packaged builds.
10. Production GUI integration against live Draft Omen services requires TUI
    parity. An isolated runnable PySide6/QML mockup may proceed earlier only
    with deterministic representative mock data and the approved session
    contract.

## Applying the boundary

- Add shared behavior to domain services or the live session, then expose only
  structured immutable state needed by frontends.
- Keep scheduling and event-loop marshalling in the active frontend adapter.
- Keep terminal formatting in Textual or plain-watch code and visual formatting
  in QML; never publish Rich renderables, widgets, `QObject` instances, or QML
  values from the live session.
- Model user actions as commands with explicit fields. Do not expose mutable
  collections, callbacks that bypass the session, or setters for domain state.
- Use the mock provider only for deterministic visual development. It must
  implement the same state and command boundary as the production adapter and
  must not become an alternate source of domain behavior.

## Verification

For every affected layer, run its focused tests and the full repository CI
workflow. Confirm core and application imports remain frontend-neutral, QML
linting covers changed modules, and packaged changes include a real application
launch check when packaging is in scope.
