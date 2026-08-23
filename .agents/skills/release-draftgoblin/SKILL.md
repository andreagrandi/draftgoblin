---
name: release-draftgoblin
description: >-
  Publish a Draftgoblin version through the complete version bump, pull request,
  merge, tag, GitHub Actions, PyPI, and Homebrew verification workflow. Use
  whenever the
  user says "release X.Y.Z", "publish version X.Y.Z", "cut a Draftgoblin
  release", or otherwise asks to ship a new Draftgoblin version to PyPI.
---

# Release Draftgoblin

Normal pushes and merges to `master` do not publish a release. Only pushing a
tag matching `v*` starts `.github/workflows/release.yml`.

An explicit request containing the target version authorizes all release-scoped
mutations: version edit, commit, push, ready PR creation, CI monitoring, PR
merge, annotated tag creation and push, release monitoring, and public install
verification. Do not ask for those permissions again or stop after creating the
PR. This authorization does not cover unrelated changes.

If the request omits the exact `X.Y.Z` version, ask for it. Never infer a version.

## Preflight

1. Read `AGENTS.md` and `docs/releasing.md`.
2. Use `gh` for every GitHub operation and confirm `gh auth status`.
3. Confirm the worktree is clean. Preserve and report unrelated changes.
4. Check the requested version is newer than `uv version --short`.
5. Confirm neither remote tag `vX.Y.Z` nor PyPI version `X.Y.Z` exists.
6. Confirm the `HOMEBREW_TAP_TOKEN` Actions secret is configured.

PyPI versions are immutable. If the requested version already exists, stop and
ask for a newer version.

## Prepare and merge the version PR

Start from current `master`:

```bash
git switch master
git pull --ff-only
git switch -c release-X.Y.Z
uv version X.Y.Z
```

Inspect the version diff and run:

```bash
uv run nox -s ci
```

Stage only the version files, run `git diff --cached --check`, and commit with
`Release X.Y.Z`. Push the branch and open a ready PR against `master` with the
mandatory `AGENTS.md` PR template. Use `gh pr checks --watch`, then merge the
green PR with the repository's merge method and delete its remote branch.

Do not tag the release branch or an unmerged commit.

## Tag and publish

Refresh `master`, confirm it reports `X.Y.Z`, and confirm the tag is still absent:

```bash
git switch master
git pull --ff-only
uv version --short
git tag -a vX.Y.Z -m "Draftgoblin X.Y.Z"
git push origin vX.Y.Z
```

Find the exact `Publish release` run for tag `vX.Y.Z` with `gh`, then
watch it through completion. The workflow must pass build validation plus
macOS and Windows wheel smoke tests before publishing, then generate, install,
test, and publish the matching Homebrew formula.

If an environment approval is required and the current identity cannot approve
it, ask the user once and continue monitoring after approval.

## Failure handling

Inspect failures with `gh run view --log-failed`. Fix workflow or packaging
failures on a new branch through another green PR.

Only recreate a failed tag when the publish job never succeeded and PyPI
confirms the version is absent. If PyPI contains the version, never move or
delete the tag; prepare a newer patch release instead.

## Verify the public release

Use fresh temporary uv cache and tool directories to install
`draftgoblin==X.Y.Z` from PyPI, then run `draftgoblin --version`. Update the tap,
install or upgrade the Homebrew formula, and check its version separately. Confirm:

- the release workflow concluded successfully;
- the public PyPI version page exists;
- `andreagrandi/homebrew-tap` contains `Formula/draftgoblin.rb` for the release;
- the installed command reports exactly `X.Y.Z`;
- local `master` is clean and synchronized.

Report the version, tag, workflow URL, PyPI URL, and PyPI plus Homebrew install
commands. Do not create a separate GitHub Release unless the user explicitly
requests one.
