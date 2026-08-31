---
name: gh-issue
description: >-
  Create a GitHub issue/ticket for the Draft Omen repo. Use whenever the user
  wants to file a bug, create a ticket, open an issue, report a problem, or
  request a feature/enhancement/documentation change for andreagrandi/draftomen.
---

# Create a GitHub issue for draftomen

Use this workflow to file issues on `andreagrandi/draftomen` and add them
to the **Draft Omen** project board:

`https://github.com/users/andreagrandi/projects/3`

Draft first. Issue creation is outward-facing: show the title, body, labels,
priority, area, and implementation classification, let the user edit, and only
run `gh issue create` after the user approves.

If the description is too thin for a clear Problem and at least one Acceptance
Criterion, ask 1-2 concise questions first. If priority or area is not stated,
ask before creating the issue.

If the user explicitly asks for an epic, draft one parent epic plus
implementation-sized child issues, show the complete proposed decomposition,
and only create them after the user approves.

## Classify

Apply exactly two labels:

- Repo label: always `draftomen`.
- Type label: exactly one of `bug`, `enhancement`, or `documentation`.

Do not invent labels. Area belongs in the project Area field, not labels. Other
repo labels such as `duplicate`, `question`, `help wanted`, or `wontfix` are not
used for new work-item creation unless the user explicitly requests them.

Verified label IDs are reference-only; pass label names to `gh issue create`.

| Label | REST ID | Node ID |
|-------|---------|---------|
| `draftomen` | `11407200945` | `LA_kwDOTMcZ0s8AAAACp-wSsQ` |
| `bug` | `11407101817` | `LA_kwDOTMcZ0s8AAAACp-qPeQ` |
| `documentation` | `11407101837` | `LA_kwDOTMcZ0s8AAAACp-qPjQ` |
| `enhancement` | `11407101867` | `LA_kwDOTMcZ0s8AAAACp-qPqw` |

Priority defaults are not allowed in this repo workflow. Ask the user if they
did not state priority.

- High: app crashes, data loss or corruption, draft pick recommendations are
  completely broken, Arena integration cannot read draft state, or `master` CI
  is red.
- Medium: important user-facing bug or feature with a workaround, degraded
  recommendations in a single flow, or release-blocking polish that does not
  stop core use.
- Low: cosmetic polish, docs, refactor/tech debt, minor developer experience,
  or nice-to-have enhancement.

Area is a required project field. Ask the user if it is not stated or inferable.

- Draft Engine: pick recommendations, card ratings/scoring, draft strategy
  logic.
- Card Data: card and set databases, data ingestion, ratings sources, data
  updates.
- Arena Integration: MTG Arena log parsing, client detection, reading draft
  state from Arena.
- UX: user-facing copy, visual layout, accessibility, flows, forms, empty/error
  states.
- Operations: CI, build settings, release config, project tooling.
- Testing: unit/UI tests, fixtures, mocks, test infrastructure.

## Implementation Classification

Classify every implementation issue with:

- Estimated size: `S`, `M`, `L`, or `XL`
- Orchestration risk: `Normal` or `High`
- Reason: one concise sentence

Do not recommend a model.

Size is approximate; do not estimate exact LOC.

- `S`: small, isolated, low-integration change.
- `M`: normal feature or bug in one main subsystem or several related files.
- `L`: multiple components, meaningful integration, several acceptance criteria,
  or non-trivial lifecycle/state behavior.
- `XL`: broad cross-cutting work involving many subsystems, significant
  coordination, migrations, complex networking/cache/offline behavior, multiple
  runtime/UI surfaces, or many independent requirements.

Use `High` orchestration risk when the work materially involves security or
hostile input, concurrency/locking, migrations, cache consistency,
networking/offline behavior, public API/schema compatibility, complex lifecycle
or state transitions, multiple runtime/UI surfaces, difficult backwards
compatibility, or failures likely to survive ordinary tests.

Otherwise use `Normal`.

Size and risk are independent. Classification is advisory and may be revised
later during repository-aware implementation planning.

## Body Template

Use this template unless the user provides a better structure:

```markdown
## Problem

<What's wrong or missing, and where in the app>

## Proposed change

<What should happen instead>

## Acceptance Criteria

- [ ] <Done condition 1>
- [ ] <Done condition 2>

## Implementation classification

- **Estimated size:** <S|M|L|XL>
- **Orchestration risk:** <Normal|High>
- **Reason:** <one concise sentence>
```

Title: short, imperative or problem-focused, with no component prefix.

## Epic Workflow

When the user explicitly asks for an epic:

- Draft one parent epic and implementation-sized child issues.
- Prefer one independently reviewable and mergeable outcome per child.
- Keep tightly coupled behavior together.
- Keep the repository working after each child.
- Make real technical dependencies explicit.
- Avoid one giant child issue when sensible decomposition exists.
- Classify the epic and every child independently.
- Do not automatically inherit the epic's size or risk into child issues.
- Show the full epic and child decomposition and wait for user approval before
  creating anything.

Use this parent epic body:

```markdown
## Problem

<Overall problem or missing capability>

## Goal

<What the completed epic should achieve>

## Scope

<Major areas covered>

## Out of scope

<Important exclusions, if any>

## Child issues

- [ ] <Child issue 1>
- [ ] <Child issue 2>

## Acceptance Criteria

- [ ] <Epic-level completion condition 1>
- [ ] <Epic-level completion condition 2>

## Epic classification

- **Overall size:** <S|M|L|XL>
- **Overall orchestration risk:** <Normal|High>
- **Reason:** <one concise sentence>
```

Use the normal issue body template for each child and add:

```markdown
## Dependencies

<Prerequisite issue(s), follow-up issue(s), or None>
```

## File It After Approval

Optional duplicate check:

```sh
gh issue list --repo andreagrandi/draftomen --search "<keywords>" --state open
```

Create the issue:

```sh
URL=$(gh issue create --repo andreagrandi/draftomen \
  --title "<title>" \
  --body "<body>" \
  --label "draftomen" \
  --label "<bug|enhancement|documentation>")
```

Add it to the Draft Omen project and capture the item ID:

```sh
ITEM_ID=$(gh project item-add 3 --owner andreagrandi \
  --url "$URL" --format json --jq .id)
```

Project ID: `PVT_kwHOAAm1584BcXPu`

Set Priority:

- High: `9253744f`
- Medium: `9664f46f`
- Low: `46883d66`

```sh
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BcXPu \
  --field-id PVTSSF_lAHOAAm1584BcXPuzhXAwaU \
  --single-select-option-id <priority-option-id>
```

Set Area:

- Draft Engine: `9a9b1e4b`
- Card Data: `36a55847`
- Arena Integration: `8773e939`
- UX: `72d9c16b`
- Operations: `cd7eec9b`
- Testing: `8a3248da`

```sh
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BcXPu \
  --field-id PVTSSF_lAHOAAm1584BcXPuzhXAwaY \
  --single-select-option-id <area-option-id>
```

Set Status to Todo:

```sh
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BcXPu \
  --field-id PVTSSF_lAHOAAm1584BcXPuzhXApdY \
  --single-select-option-id f75ad846
```

For an epic, repeat issue creation and project-field setup for the parent epic
and every child issue.

Report the issue URL and the selected type, priority, area, size, and
orchestration risk.

For an epic, report the parent epic and all child issues in dependency order,
including each issue's size and orchestration risk.

## Link Dependencies

Only do this for a normal issue when the user explicitly says the issue is
blocked by or blocking another issue.

For an approved epic decomposition, create dependency links that were part of
the approved child-ticket structure.

`gh` has no native issue-dependency command, so use the REST API.

The body uses the other issue's database `id`, not its `#number`.

```sh
# This issue <N> is blocked by <BLOCKER>.

BLOCKER_ID=$(gh api repos/andreagrandi/draftomen/issues/<BLOCKER> --jq .id)

gh api --method POST -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/andreagrandi/draftomen/issues/<N>/dependencies/blocked_by \
  -F issue_id="$BLOCKER_ID"
```

List or remove blocked-by relationships:

```sh
gh api repos/andreagrandi/draftomen/issues/<N>/dependencies/blocked_by \
  -H "X-GitHub-Api-Version: 2026-03-10" --jq '.[] | "#\(.number) \(.title)"'

gh api --method DELETE -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/andreagrandi/draftomen/issues/<N>/dependencies/blocked_by/<BLOCKER_ID>
```

## Notes

- Project fields were verified with `gh project field-list 3 --owner andreagrandi`.
- If project commands fail because of missing scopes, run `gh auth refresh -s project`.
- Never create the GitHub issue without adding it to the Draft Omen project and
  setting Priority, Area, and Status.
- Never create an epic or child issues before the user approves the proposed
  decomposition.
- Do not recommend a specific model in issue content.
