---
name: release-draftomen
description: >-
  Publish a Draft Omen version through the complete version bump, pull request,
  merge, tag, GitHub Actions, PyPI, and Homebrew verification workflow. Use
  whenever the
  user says "release X.Y.Z", "publish version X.Y.Z", "cut a Draft Omen
  release", or otherwise asks to ship a new Draft Omen version to PyPI.
---

# Release Draft Omen

Normal pushes and merges to `master` do not publish a release. Only pushing a
tag matching `v*` starts `.github/workflows/release.yml`.

An explicit request containing the target version authorizes all release-scoped
mutations: version edit, commit, push, ready PR creation, CI monitoring, PR
merge, annotated tag creation and push, release monitoring, and public install
verification. Do not ask for those permissions again or stop after creating the
PR. This authorization does not cover unrelated changes.

If the request omits the exact `X.Y.Z` version, ask for it. Never infer a version.

## Preflight

1. Read `AGENTS.md`, `docs/releasing.md`, and `CHANGELOG.md`.
2. Use `gh` for every GitHub operation and confirm `gh auth status`.
3. Confirm the worktree is clean. Preserve and report unrelated changes.
4. Check the requested version is newer than `uv version --short`.
5. Confirm neither remote tag `vX.Y.Z` nor PyPI version `X.Y.Z` exists.
6. Confirm the `HOMEBREW_TAP_DEPLOY_KEY` Actions secret is configured.

PyPI versions are immutable. If the requested version already exists, stop and
ask for a newer version.

## Prepare and merge the version PR

Start from current `master`:

```bash
git switch master
git pull --ff-only
git switch -c release-X.Y.Z
```

Before bumping the package version, promote the non-empty body under the exact
`## [Unreleased]` heading in `CHANGELOG.md` to:

```text
## [X.Y.Z] - YYYY-MM-DD
```

Use the UTC release date, preserve the entries unchanged, and restore an empty
`## [Unreleased]` heading immediately above the new dated section. Then run:

```bash
uv version X.Y.Z
```

Inspect the version and changelog diff and run:

```bash
uv run nox -s ci
```

Stage only the version files and `CHANGELOG.md`, run `git diff --cached --check`,
and commit with `Release X.Y.Z`. Push the branch and open a ready PR against
`master` with the mandatory `AGENTS.md` PR template. Use `gh pr checks --watch`,
then merge the green PR with the repository's merge method and delete its remote
branch.

Do not tag the release branch or an unmerged commit.

## Tag and publish

Refresh `master`, confirm it reports `X.Y.Z`, and confirm the tag is still absent:

```bash
git switch master
git pull --ff-only
uv version --short
git tag -a vX.Y.Z -m "Draft Omen X.Y.Z"
git push origin vX.Y.Z
```

Find the exact `Publish release` run for tag `vX.Y.Z` with `gh`, then watch it
through completion. The workflow checks out the tagged repository and extracts
the non-empty body under the exact `## [X.Y.Z] - YYYY-MM-DD` section from
`CHANGELOG.md` for the stable GitHub Release notes. A missing, duplicate, or
empty section fails before release publication; the workflow never falls back
to generated notes. It must pass build validation plus macOS and Windows wheel
smoke tests before publishing, then generate, install, test, and publish the
matching Homebrew formula and create or update the public GitHub Release with
the native bundle assets.

If an environment approval is required and the current identity cannot approve
it, ask the user once and continue monitoring after approval.

## Failure handling

Inspect failures with `gh run view --log-failed`. Fix workflow or packaging
failures on a new branch through another green PR.

After a failed publish, do not immediately recreate, move, or delete `vX.Y.Z`.
First confirm both that the publish never succeeded and that PyPI does not
contain `X.Y.Z`. Only after both checks confirm that no publication succeeded
and the version is absent from PyPI may you recreate or move the failed tag
and retry publication. If PyPI contains `X.Y.Z`, never move or delete
`vX.Y.Z`; use a newer patch release through a new release PR instead.

## Verify the public release

Use fresh temporary uv cache and tool directories to install
`draftomen==X.Y.Z` from PyPI, then run `draftomen-tui --version` and a
deterministic `draftomen --provider mock --smoke-test`. Update the tap, install
or upgrade the Homebrew formula, and check its `draftomen-tui --version`
output separately.
Inspect `gh release view vX.Y.Z` and confirm it is published, its body contains
the promoted dated changelog entries, and its native assets and checksum file
are present. Confirm:

- the release workflow concluded successfully;
- the public GitHub Release for `vX.Y.Z` exists with the expected changelog body;
- the public PyPI version page exists;
- `andreagrandi/homebrew-tap` contains `Formula/draftomen.rb` for the release;
- the installed `draftomen-tui` command reports exactly `X.Y.Z`;
- local `master` is clean and synchronized.

Report the version, tag, workflow URL, GitHub Release URL, PyPI URL, and PyPI
plus Homebrew install commands.
