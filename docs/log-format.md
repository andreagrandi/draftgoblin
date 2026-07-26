# MTG Arena Quick Draft log format

This document records the current Arena `Player.log` tokens observed in the sanitized fixture at `tests/fixtures/quick-draft-msh-player.log`.

## Fixture

- Source: one complete Quick Draft on macOS with Detailed Logs (Plugin Support) enabled.
- Event: `QuickDraft_MSH_20260702`.
- Set code: `MSH`, parsed from the event name segment after `QuickDraft_`.
- Draft shape: 3 packs, 14 picks per pack, 42 total picks.
- Numbering: `PackNumber` and `PickNumber` are zero-indexed in the log.

The fixture keeps the draft protocol payloads and all card `grpId` values intact. Personal account identifiers, request IDs, session IDs, course IDs, local paths, and inventory/cosmetic data were pseudonymized or removed because parsing does not need them.

## Account identity

Arena logs account identity near session start and match authentication. The most useful parser token is `authenticateResponse`:

```log
{ "transactionId": "00000000-0000-4000-8000-000000000001", "requestId": 1, "timestamp": "639186578104553274", "authenticateResponse": { "clientId": "FIXTURECLIENTID1234567890", "sessionId": "00000000-0000-4000-8000-000000000002", "screenName":"FixturePlayer" } }
```

Use `authenticateResponse.clientId` as the MTGA account key for current logs and `screenName` for display. `sessionId` is session-scoped and should not be used as the account key. Cross-patch stability still needs future revalidation; if `clientId` disappears, fall back to `screenName` plus a manual `--account` override. When `authenticateResponse.screenName` is missing or only repeats the client id, the parser uses the nearby login UI line as a display-name fallback:

```log
[Accounts - Login] Logged in successfully. Display Name: FixturePlayer#12345
```

Draftgoblin stores the latest verified display name in a separate per-account
profile in its app data directory. That profile labels every recovered draft for
that account, including legacy snapshots written before display-name support.
When Arena emits a login line without its matching authentication response, the
TUI first tries to uniquely match that display name, with or without its numeric
`#` discriminator, to a saved account profile. It can otherwise associate the
login only with recovered drafts whose Quick Draft course id is present in the
same session's course snapshot; if neither method is unambiguous, it leaves the
account unresolved rather than guessing.

## Quick Draft start

A paid Quick Draft entry appears as an `EventJoin` request followed by a course payload whose `InternalEventName` is the event id and whose `CurrentModule` is `BotDraft`:

```log
[UnityCrossThreadLogger]==> EventJoin {"id":"00000000-0000-4000-8000-000000000003","request":"{\"EventName\":\"QuickDraft_MSH_20260702\",\"EntryCurrencyType\":\"Gold\",\"EntryCurrencyPaid\":5000,\"CustomTokenId\":null,\"EventChoice\":\"\",\"DebugIgnoreEntryLimits\":false}"}
{"Course":{"CourseId":"00000000-0000-4000-8000-000000000004","InternalEventName":"QuickDraft_MSH_20260702","CurrentModule":"BotDraft","ModulePayload":"","CourseDeckSummary":{"Attributes":[]},"CardPool":[],"CardStyles":[]},"InventoryInfo":{"SeqId":23,"Changes":[]}}
```

Persist draft state by `(account clientId, CourseId/InternalEventName)`. The event id gives the set code and the course id disambiguates a concrete run of that event.

Arena can also emit Quick Draft course snapshots after the course has moved to another module, such as `DeckSelect`.
Those snapshots are not draft-start events and should be ignored; pack presentation and completion are parsed from the module payload lines below.

## Pack presentation

The initial P1P1 pack is requested with `BotDraftDraftStatus`; the response body has `CurrentModule: "BotDraft"` and a string-encoded JSON `Payload`:

```log
[UnityCrossThreadLogger]==> BotDraftDraftStatus {"id":"00000000-0000-4000-8000-000000000005","request":"{\"EventName\":\"QuickDraft_MSH_20260702\"}"}
<== BotDraftDraftStatus(00000000-0000-4000-8000-000000000005)
{"CurrentModule":"BotDraft","Payload":"{\"Result\":\"Success\",\"EventName\":\"QuickDraft_MSH_20260702\",\"DraftStatus\":\"PickNext\",\"PackNumber\":0,\"PickNumber\":0,\"NumCardsToPick\":1,\"DraftPack\":[\"104894\",\"104976\",\"105080\",\"104995\",\"105027\",\"105030\",\"105170\",\"104932\",\"104893\",\"105091\",\"104969\",\"105097\",\"104979\",\"105164\"],\"PackStyles\":[],\"PickedCards\":[],\"PickedStyles\":[]}"}
```

Parse `Payload.DraftPack` as the offered card `grpId` list. The IDs are strings in this fixture and should be normalized to integers by the parser. Draft state retains each offered pack and its pool snapshot. When the latest offer has no chosen card yet, cycling back to that account reconstructs and rescores the pending pack instead of showing an empty recovered-draft view.

After each chosen card, the response to `BotDraftDraftPick` presents the next pack using the same `Payload` fields:

```log
<== BotDraftDraftPick(00000000-0000-4000-8000-000000000006)
{"CurrentModule":"BotDraft","Payload":"{\"Result\":\"Success\",\"EventName\":\"QuickDraft_MSH_20260702\",\"DraftStatus\":\"PickNext\",\"PackNumber\":0,\"PickNumber\":1,\"NumCardsToPick\":1,\"DraftPack\":[\"104933\",\"105063\",\"104949\",\"105020\",\"105036\",\"105003\",\"104989\",\"104948\",\"104971\",\"105134\",\"105086\",\"105007\",\"105166\"],\"PackStyles\":[],\"PickedCards\":[\"105097\"],\"PickedStyles\":[]}"}
```

## Chosen-card event

The chosen card is sent as a `BotDraftDraftPick` request. `PickInfo.CardIds` contains the selected `grpId`; Quick Draft picks one card, so use the first element.

```log
[UnityCrossThreadLogger]==> BotDraftDraftPick {"id":"00000000-0000-4000-8000-000000000006","request":"{\"EventName\":\"QuickDraft_MSH_20260702\",\"PickInfo\":{\"EventName\":\"QuickDraft_MSH_20260702\",\"CardIds\":[\"105097\"],\"PackNumber\":0,\"PickNumber\":0}}"}
```

The response `Payload.PickedCards` is a cumulative pool snapshot and can be used as a cross-check, but the request is the direct chosen-card event.

## Completion signal

The current log includes an explicit completion payload immediately after the final pick:

```log
<== BotDraftDraftPick(00000000-0000-4000-8000-000000000047)
{"CurrentModule":"DeckSelect","Payload":"{\"Result\":\"Success\",\"EventName\":\"QuickDraft_MSH_20260702\",\"DraftStatus\":\"Completed\",\"PackNumber\":2,\"PickNumber\":13,\"NumCardsToPick\":1,\"DraftPack\":[],\"PackStyles\":[],\"PickedCards\":[\"105030\",\"105097\",\"104989\",\"105134\",\"105003\",\"105054\",\"105037\",\"105070\",\"105054\",\"105117\",\"105014\",\"105084\",\"105034\",\"104997\",\"105037\",\"105006\",\"104996\",\"105047\",\"105032\",\"105003\",\"105017\",\"105049\",\"105003\",\"104983\",\"105005\",\"105013\",\"104998\",\"105033\",\"105000\",\"105031\",\"104995\",\"104986\",\"105004\",\"105164\",\"105005\",\"104995\",\"104911\",\"105117\",\"105053\",\"105182\",\"104989\",\"105002\"],\"PickedStyles\":[]}"}
Wotc.Mtga.Events.LimitedPlayerEvent:CompleteDraft()
```

The parser should auto-trigger the deck builder when `Payload.DraftStatus == "Completed"`. Fallback inference remains safe if Arena drops the explicit status: `PackNumber == 2`, `PickNumber == 13`, `DraftPack == []`, and 42 `PickedCards`.

## Rotation behavior

On macOS, the current log is at `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`. Arena rotates it on restart by moving the previous session to `Player-prev.log` and starting a new `Player.log`. Live recovery should scan `Player-prev.log` before `Player.log` on startup when attempting to reconstruct an in-progress draft.

## Reference cross-check

Reference implementations confirm the current fields and show token drift:

- `bstaple1/MTGA_Draft_17Lands` uses older marker strings `BotDraft_DraftStatus` and `BotDraft_DraftPick` in `src/constants.py`.
- `mtgatool/mtgatool-desktop` parses current `BotDraftDraftStatus` payload fields including `EventName`, `DraftStatus`, `PackNumber`, `PickNumber`, `DraftPack`, and `PickedCards`.
- `manasight/manasight-parser` documents current Quick Draft flow as `BotDraftDraftStatus` for initial pack presentation and `BotDraftDraftPick` for pick requests plus following pack responses.

No reference code was copied.

## PRD open questions answered

1. Current Quick Draft tokens are `BotDraftDraftStatus` for initial status and `BotDraftDraftPick` for pick requests/responses. The older underscore forms are not present in this fixture.
2. Account identity is available from `authenticateResponse.clientId`; use `screenName` or the login display-name line only as display metadata. Cross-patch stability is not provable from one fixture, so the documented fallback is `screenName` plus a manual account override if the token changes.
3. Completion is explicit via `Payload.DraftStatus == "Completed"` in `CurrentModule == "DeckSelect"`; fallback is final pick plus empty `DraftPack` plus 42-card `PickedCards` pool.
