---
name: gh-issue
description: >-
  Create, classify, split, and organize GitHub issues for andreagrandi/draftomen.
  Use when filing issues, reviewing ticket metadata, creating epics or native
  sub-issues, linking dependencies, or updating the Draft Omen project.
---

# Manage Draft Omen issues

Repository: `andreagrandi/draftomen` · Project: `https://github.com/users/andreagrandi/projects/3`

See [REFERENCE.md](REFERENCE.md) for classifications, templates, IDs, and API patterns.

## Non-negotiable rules

- Draft outward-facing issue content first. Show title, body, labels, Priority,
  Area, Size, and risk; mutate GitHub only after explicit approval.
- One agent owns all authenticated GitHub I/O. Every `gh`, `issue://`, `pr://`,
  and GitHub-backed read in every subagent shares the user's API budget.
- Delegated repository research receives already-fetched issue data through a
  `local://` artifact and MUST NOT call GitHub.
- Never parallelize GitHub mutations. Batch independent ProjectV2 mutations
  into one GraphQL request.
- Never run GraphQL schema introspection during routine work. Use the stable
  IDs and patterns in [REFERENCE.md](REFERENCE.md).
- Never repeat a project or issue query merely to apply a different `jq`
  projection. Retain the first response and parse it locally.
- Never loop over `gh project item-edit --url ... --field ... --value ...`.
  Friendly-name resolution can issue extra lookups; use node and field IDs.

## Call budget

- Budgets count tool invocations; prefer explicit one-request APIs because high-level `gh` may fan out.
- Single issue: at most 6 calls after approval: create, add to the project, and set four fields by ID.
- Multi-issue or epic: write the API call graph before execution. Target no
  more than `2N + D + 8` calls for `N` issues and `D` dependency links.
- A duplicate search, native parent link, or dependency link may add a call.
  Any other budget increase requires a concrete reason before execution.
- Above 10 calls, include `rateLimit { cost remaining resetAt }` in an existing
  query; never poll or use REST `/rate_limit` as a GraphQL authority.
- If quota is low, stop nonessential reads and switch to targeted node-ID
  queries plus batched mutations. Do not retry before `resetAt`.

## Gather once

1. Single issue: fetch its body, labels, project fields, native parent/children,
   and requested dependencies once.
2. Project batch: fetch the project JSON once with the narrowest useful limit.
   Preserve the response in memory or `/tmp/agents`; derive every view locally.
3. Existing issue split: perform the required `pr-scope-guidance` assessment.
   Fetch GitHub metadata centrally; delegate only genuinely independent
   repository/code boundaries.
4. Ask only for Priority or Area when neither is stated nor safely inferable.
   Do not query GitHub for information already present in the issue or project
   response.

## Draft and classify

- Every issue gets exactly three labels: `draftomen`, one of `bug`,
  `enhancement`, or `documentation`, and `size: <S|M|L|XL>`.
- Project fields are required: Priority, Area, Status `Todo`, and Size matching
  the size label.
- Use the body and epic templates in [REFERENCE.md](REFERENCE.md).
- For an epic or split, prefer one independently reviewable outcome per child,
  make dependencies explicit, classify every child independently, and preserve
  the native parent-child relationship.
- Never recommend a model in issue content.

## Execute after approval

### One issue

1. Create it with `gh issue create`, passing label names.
2. Add it with `gh project item-add` and capture the returned project item ID.
3. Set all four project fields with ID-based `gh project item-edit` commands.
4. Add explicitly requested native parent or dependency links.

### Two or more issues

1. Create each approved issue; retain every URL and number.
2. Resolve all issue node/database IDs in one targeted GraphQL query.
3. Add every issue to the project in one aliased
   `addProjectV2ItemById` mutation; retain returned item IDs.
4. Set Priority, Area, Status, and Size for every item in one aliased
   `updateProjectV2ItemFieldValue` mutation.
5. Attach native sub-issues and dependency links with the REST patterns in
   [REFERENCE.md](REFERENCE.md). Do not add redundant transitive dependencies.

## Verify once

- One targeted GraphQL query must return all affected project items and their
  Priority, Area, Status, and Size; compare locally with the approved values.
- One parent sub-issues query verifies all native children.
- Query blocked-by endpoints only for children that should have direct
  dependencies.
- Verify labels from the same issue/sub-issue response when present.
- Do not re-run successful mutations as verification.
- Report URLs, type, Priority, Area, Size, risk, parent links, dependencies,
  verification result, and actual authenticated-call count.
