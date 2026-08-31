# Draft Omen issue reference

## Stable GitHub metadata

Repository: `andreagrandi/draftomen`

Project ID: `PVT_kwHOAAm1584BcXPu`

| Field | Field ID | Options |
|---|---|---|
| Priority | `PVTSSF_lAHOAAm1584BcXPuzhXAwaU` | High `9253744f`; Medium `9664f46f`; Low `46883d66` |
| Area | `PVTSSF_lAHOAAm1584BcXPuzhXAwaY` | Draft Engine `9a9b1e4b`; Card Data `36a55847`; Arena Integration `8773e939`; UX `72d9c16b`; Operations `cd7eec9b`; Testing `8a3248da` |
| Status | `PVTSSF_lAHOAAm1584BcXPuzhXApdY` | Todo `f75ad846`; In Progress `47fc9ee4`; Done `98236657` |
| Size | `PVTSSF_lAHOAAm1584BcXPuzhg4Fu8` | S `592a51b4`; M `d58e4a9f`; L `8f1c31eb`; XL `62663f98` |

Pass label names to `gh issue create`:

- Repository: `draftomen`
- Type: `bug`, `enhancement`, or `documentation`
- Size: `size: S`, `size: M`, `size: L`, or `size: XL`

## Classification

Priority has no default. Ask when it is not stated or inferable.

- High: crashes, data loss/corruption, broken recommendations, unreadable Arena
  draft state, or red `master` CI.
- Medium: important user-facing work with a workaround, degraded behavior in one
  flow, or release-blocking polish that does not stop core use.
- Low: cosmetic polish, docs, refactor/tech debt, minor developer experience, or
  a nice-to-have enhancement.

Area:

- Draft Engine: recommendations, ratings, scoring, and strategy.
- Card Data: card/set databases, ingestion, ratings sources, and data updates.
- Arena Integration: log parsing, client detection, and draft-state ingestion.
- UX: copy, layout, accessibility, flows, forms, and UI states.
- Operations: CI, builds, releases, hosting, and project tooling.
- Testing: tests, fixtures, mocks, and test infrastructure.

Size is approximate; never estimate exact LOC in issue content.

- S: isolated, low-integration change.
- M: one main subsystem or several related files.
- L: multiple components, meaningful integration, several acceptance criteria,
  or non-trivial lifecycle/state behavior.
- XL: broad cross-cutting work across many subsystems, migrations, complex
  networking/cache/offline behavior, multiple runtime/UI surfaces, or many
  independent requirements.

Only `S` and `M` issues are implementation-ready. `L` and `XL` issues are
epic-shaped and must be decomposed into native `S`/`M` sub-issues before
implementation is scheduled (see SKILL.md, "Size and implementation
readiness").

Use High orchestration risk for security/hostile input, concurrency/locking,
migrations, cache consistency, networking/offline behavior, public schema/API
compatibility, complex lifecycle/state transitions, multiple runtime/UI
surfaces, difficult compatibility, or failures likely to survive ordinary
tests. Otherwise use Normal. Size and risk are independent.

## Normal issue body

```markdown
## Problem

<What is wrong or missing and where>

## Proposed change

<What should happen instead>

## Acceptance Criteria

- [ ] AC1: <Observable done condition>
- [ ] AC2: <Observable done condition>

## Implementation classification

- **Estimated size:** <S|M|L|XL>
- **Orchestration risk:** <Normal|High>
- **Reason:** <One concise sentence>
```

Title: short, imperative or problem-focused, with no component prefix.

For a child issue, add:

```markdown
## Dependencies

<Direct prerequisite issue(s), follow-up issue(s), or None>
```

## Epic body

```markdown
## Problem

<Overall problem>

## Goal

<Completed outcome>

## Scope

<Major covered areas>

## Out of scope

<Important exclusions>

## Child issues

- [ ] <Child outcome>
- [ ] <Child outcome>

## Acceptance Criteria

- [ ] <Epic-level completion condition>
- [ ] <Epic-level completion condition>

## Epic classification

- **Overall size:** <S|M|L|XL>
- **Overall orchestration risk:** <Normal|High>
- **Reason:** <One concise sentence>
```

## Efficient single-issue execution

```sh
URL=$(gh issue create --repo andreagrandi/draftomen \
  --title "<title>" --body "<body>" \
  --label draftomen --label "<type>" --label "size: <S|M|L|XL>")

ITEM_ID=$(gh project item-add 3 --owner andreagrandi \
  --url "$URL" --format json --jq .id)
```

Set each field with node IDs; never resolve by URL/name in the edit loop:

```sh
gh project item-edit --id "$ITEM_ID" \
  --project-id PVT_kwHOAAm1584BcXPu \
  --field-id <field-id> --single-select-option-id <option-id>
```

## Efficient multi-issue execution

After creation, resolve every issue node ID and REST database ID in one query:

```graphql
query {
  repository(owner: "andreagrandi", name: "draftomen") {
    i123: issue(number: 123) { id databaseId url }
    i124: issue(number: 124) { id databaseId url }
  }
  rateLimit { cost remaining resetAt }
}
```

Add every issue to the project in one request:

```graphql
mutation {
  i123: addProjectV2ItemById(input: {
    projectId: "PVT_kwHOAAm1584BcXPu"
    contentId: "<issue-node-id>"
  }) { item { id } }
  i124: addProjectV2ItemById(input: {
    projectId: "PVT_kwHOAAm1584BcXPu"
    contentId: "<issue-node-id>"
  }) { item { id } }
}
```

Set every field in one request. Add one alias per item and field:

```graphql
mutation {
  i123Priority: updateProjectV2ItemFieldValue(input: {
    projectId: "PVT_kwHOAAm1584BcXPu"
    itemId: "<project-item-id>"
    fieldId: "PVTSSF_lAHOAAm1584BcXPuzhXAwaU"
    value: { singleSelectOptionId: "<priority-option-id>" }
  }) { projectV2Item { id } }
  i123Area: updateProjectV2ItemFieldValue(input: {
    projectId: "PVT_kwHOAAm1584BcXPu"
    itemId: "<project-item-id>"
    fieldId: "PVTSSF_lAHOAAm1584BcXPuzhXAwaY"
    value: { singleSelectOptionId: "<area-option-id>" }
  }) { projectV2Item { id } }
}
```

Include Status and Size aliases in that same mutation. Execute through one
`gh api graphql -f query='<mutation>'` process. Do not issue one mutation per
alias.

## Native relationships

The relationship endpoints require REST database IDs, which the batched issue
query already returned.

Attach a native child:

```sh
gh api --method POST -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/andreagrandi/draftomen/issues/<parent>/sub_issues \
  -F sub_issue_id=<child-database-id>
```

Add a direct blocked-by relationship:

```sh
gh api --method POST -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/andreagrandi/draftomen/issues/<issue>/dependencies/blocked_by \
  -F issue_id=<blocker-database-id>
```

Do not query an issue again just to recover a database ID. Do not add transitive
blocked-by links.

## Verification query

Verify all project items in one targeted query using the item IDs returned by
the add mutation:

```graphql
query {
  nodes(ids: ["<item-id>", "<item-id>"]) {
    ... on ProjectV2Item {
      content { ... on Issue { number title labels(first: 10) { nodes { name } } } }
      fieldValues(first: 20) {
        nodes {
          ... on ProjectV2ItemFieldSingleSelectValue {
            name
            field { ... on ProjectV2SingleSelectField { name } }
          }
        }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
```

Compare the response locally with the approved specification. One parent
`sub_issues` request and only the necessary direct `blocked_by` requests finish
relationship verification.
