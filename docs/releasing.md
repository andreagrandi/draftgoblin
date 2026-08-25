# Releasing Draftgoblin

Stable Draftgoblin releases publish a Python wheel and source distribution to PyPI, then generate, install, test, and publish a Homebrew formula to `andreagrandi/homebrew-tap`. Releases use GitHub Actions and PyPI Trusted Publishing, so the repository does not store a long-lived PyPI token. Development releases are a separate GitHub prerelease path and never publish to PyPI or Homebrew.

## Release trigger

Normal pushes and merges to `master` run CI but do not publish a stable release. A stable release starts only when a tag matching `v*` is pushed, and the workflow rejects tags that do not match the version in `pyproject.toml`.

For a manually requested native development build, ask exactly `make a new dev release`. The repository's development-release skill dispatches `.github/workflows/native-bundles.yml` on `master` with a unique UTC request ID, watches that exact run, verifies the rolling prerelease and its assets, and reports the workflow and release URLs.

## Development releases

Development releases are native-only builds for manual testing. The workflow creates or updates the GitHub prerelease with the mutable tag `development`; it does not create a version tag or invoke the stable release workflow. The rolling prerelease is always available at the stable URL:

<https://github.com/andreagrandi/draftgoblin/releases/tag/development>

Each build uses this identifier:

```text
v<VERSION>-dev.<YYYYMMDD>.<RUN_NUMBER>
```

`VERSION` comes from `pyproject.toml`, `YYYYMMDD` is the UTC build date, and
`RUN_NUMBER` is `github.run_number`. The run number is scoped to the workflow,
progresses monotonically, and does not change when that run is rerun. The
release title and notes identify the same version, UTC date, and progressive
run number. For example, run 7 for version `0.2.0` on 2026-08-25 uses
`v0.2.0-dev.20260825.7` and the title `Draftgoblin v0.2.0 development build
2026-08-25 #7`.

The rolling prerelease contains exactly three unsigned assets for the current
build:

- `draftgoblin-<build-id>-unsigned-macos.tar`
- `draftgoblin-<build-id>-unsigned-windows.exe`
- `draftgoblin-<build-id>-unsigned-sha256sums.txt`

The checksum file contains SHA-256 checksums for the macOS and Windows assets.
Development releases do not publish Python packages to PyPI, update
Homebrew, or replace an immutable stable release.

## One-time setup

1. Sign in to PyPI and create a pending Trusted Publisher with:
   - PyPI project name: `draftgoblin`
   - GitHub owner: `andreagrandi`
   - GitHub repository: `draftgoblin`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`
2. Create a GitHub environment named `pypi`.
3. Configure the `pypi` environment to require manual approval before deployment.
4. Add a write-enabled SSH deploy key to `andreagrandi/homebrew-tap`.
5. Store its private key in `andreagrandi/draftgoblin` as an Actions secret named `HOMEBREW_TAP_DEPLOY_KEY`.

Once configured, tagged releases update the Homebrew formula automatically. No manual formula generation or tap update is required.

The pending publisher creates the PyPI project on the first successful release. If PyPI rejects the project name, choose a new distribution name in `pyproject.toml` while retaining the `draftgoblin` console command.

## Publish a release

1. Update the version on a feature branch:

   ```bash
   uv version <version>
   uv run nox -s ci
   ```

2. Merge the version change through the normal pull request workflow.
3. From the updated `master` branch, create and push the matching tag:

   ```bash
   git tag v<version>
   git push origin v<version>
   ```

The release workflow rejects mismatched versions, runs the full CI gate, builds and validates both distributions, installs and smoke-tests the wheel on macOS and Windows, and waits for approval before publishing. After PyPI succeeds, it resolves the immutable source archive, pins all Python resources, installs and tests the generated formula, and pushes `Formula/draftgoblin.rb` to the Homebrew tap.

## Verify the published release

Install the exact published version in an isolated environment:

```bash
uvx draftgoblin@<version> --version
brew update
brew install andreagrandi/tap/draftgoblin
draftgoblin --version
```

The Homebrew formula is updated only after the immutable PyPI release succeeds. If a PyPI release is broken, yank it with a reason and publish a corrected patch release rather than replacing its files.
