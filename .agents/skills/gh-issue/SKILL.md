---
name: gh-issue
description: >-
  Create a GitHub issue/ticket for the Draftgoblin repo. Use whenever the user
  wants to file a bug, create a ticket, open an issue, report a problem, or
  request a feature/enhancement/documentation change for andreagrandi/draftgoblin.
---

# Create a GitHub issue for draftgoblin

Use this workflow to file issues on `andreagrandi/draftgoblin` and add them
to the **Draftgoblin** project board:
`https://github.com/users/andreagrandi/projects/3`.

Draft first. Issue creation is outward-facing: show the title, body, labels,
priority, and area, let the user edit, and only run `gh issue create` after the
user approves. If the description is too thin for a clear Problem and at least
one Acceptance Criterion, ask 1-2 concise questions first. If priority or area
is not stated, ask before creating the issue.

## Classify

Apply exactly two labels:

- Repo label: always `draftgoblin`.
- Type label: exactly one of `bug`, `enhancement`, or `documentation`.

Do not invent labels. Area belongs in the project Area field, not labels. Other
repo labels such as `duplicate`, `question`, `help wanted`, or `wontfix` are not
used for new work-item creation unless the user explicitly requests them.

Verified label IDs are reference-only; pass label names to `gh issue create`.

| Label | REST ID | Node ID |
|-------|---------|---------|
| `draftgoblin` | `11407200945` | `LA_kwDOTMcZ0s8AAAACp-wSsQ` |
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
```

Title: short, imperative or problem-focused, with no component prefix.

## File It After Approval

Optional duplicate check:

```sh
gh issue list --repo andreagrandi/draftgoblin --search "<keywords>" --state open
```

Create the issue:

```sh
URL=$(gh issue create --repo andreagrandi/draftgoblin \
  --title "<title>" \
  --body "<body>" \
  --label "draftgoblin" \
  --label "<bug|enhancement|documentation>")
```

Add it to the Draftgoblin project and capture the item ID:

```sh
ITEM_ID=$(gh project item-add 3 --owner andreagrandi \
  --url "$URL" --format json --jq .id)
```

Project ID: `PVT_kwHOAAm1584BcXPu`

Set Priority:

- High: `64555943`
- Medium: `eb1638a3`
- Low: `7c646b9f`

```sh
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BcXPu \
  --field-id PVTSSF_lAHOAAm1584BcXPuzhXAwaU \
  --single-select-option-id <priority-option-id>
```

Set Area:

- Draft Engine: `844bf4b5`
- Card Data: `4f4f92cc`
- Arena Integration: `1617aa1c`
- UX: `6e4231b5`
- Operations: `9e8a8914`
- Testing: `7e7deb50`

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

Report the issue URL and the selected type, priority, and area.

## Link Dependencies

Only do this when the user explicitly says the issue is blocked by or blocking
another issue. `gh` has no native issue-dependency command, so use the REST API.
The body uses the other issue's database `id`, not its `#number`.

```sh
# This issue <N> is blocked by <BLOCKER>.
BLOCKER_ID=$(gh api repos/andreagrandi/draftgoblin/issues/<BLOCKER> --jq .id)
gh api --method POST -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/andreagrandi/draftgoblin/issues/<N>/dependencies/blocked_by \
  -F issue_id="$BLOCKER_ID"
```

List or remove blocked-by relationships:

```sh
gh api repos/andreagrandi/draftgoblin/issues/<N>/dependencies/blocked_by \
  -H "X-GitHub-Api-Version: 2026-03-10" --jq '.[] | "#\(.number) \(.title)"'

gh api --method DELETE -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/andreagrandi/draftgoblin/issues/<N>/dependencies/blocked_by/<BLOCKER_ID>
```

## Notes

- Project fields were verified with `gh project field-list 3 --owner andreagrandi`.
- If project commands fail because of missing scopes, run `gh auth refresh -s project`.
- Never create the GitHub issue without adding it to the Draftgoblin project and
  setting Priority, Area, and Status.

