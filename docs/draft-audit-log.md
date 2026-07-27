# Draft audit logging

Draftgoblin writes an independent audit trail while processing live Quick Drafts.
The log is designed for later algorithm investigations without recalculating old
decisions using newer ratings or configuration.

## Location and format

Each draft has one append-only JSON Lines file:

```text
~/.draftgoblin/audit/drafts/<account-id>/<draft-id>.jsonl
```

An explicit `--app-dir` replaces `~/.draftgoblin` in that path. Every line is a
complete JSON object with `schema_version`, `record_id`, `record_type`,
`recorded_at`, application version, account, draft, event, and set identifiers.
The writer appends a complete encoded line and flushes it to durable storage
before returning.

Records are never updated in place. Their stable IDs make repeated Arena log
events idempotent within one process and allow downstream analysis to de-duplicate
records safely if two Draftgoblin processes watch the same account concurrently.
A malformed existing audit file fails loudly instead of accepting new records
after corrupted evidence.

## Record types

`draft_started`

- Course identity and original draft start timestamp.

`decision_evaluated`

- Zero-based pack and pick coordinates plus the absolute pick index.
- Complete offered pack and pool before the pick.
- Pick-engine configuration and application version.
- Ratings dataset formats, fetch timestamps, and aggregate pair records.
- Score-normalization bounds and inferred color commitment.
- Every candidate's card metadata, resolved rating, sample counts, fallback
  source, base score, color adjustment, pair tiebreaker, final score, and rank.
- Exact card order for DG Score, 17Lands win rate, ALSA, and mana-value views.

A pending pick may have more than one evaluation when ratings finish loading or
the scoring inputs genuinely change. Each distinct evaluation gets its own
`evaluation_id`. Once its choice is recorded, startup rescans cannot add newer
evaluations to that historical pick.

`choice_made`

- The Arena card actually chosen.
- The TUI ranking mode visible at the time, or DG Score in plain watch mode.
- The recommendation at the top of that ranking.
- Whether the user followed the recommendation.
- The `decision_id` and latest `evaluation_id` available at choice time.

A choice remains useful even if Draftgoblin started after the offered pack and
therefore has no evaluation to link. In that case, the evaluation and
recommendation fields are `null`.

`draft_completed`

- Final pool, card count, completion coordinates, and whether completion was
  explicit or inferred from the final Arena payload.

## Scope

The live Textual interface and `watch --plain` write audit data. Offline `replay`,
deck building, benchmarking, and backtesting do not, because recomputed
recommendations are not evidence of what was shown during the original draft.

Audit logs remain local. Draftgoblin does not upload them, automatically delete
them, or combine them with mutable resumable state under `state/`.
