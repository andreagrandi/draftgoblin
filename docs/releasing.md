# Releasing Draftgoblin

Draftgoblin publishes a Python wheel and source distribution to PyPI, then generates, installs, tests, and publishes a Homebrew formula to `andreagrandi/homebrew-tap`. Releases use GitHub Actions and PyPI Trusted Publishing, so the repository does not store a long-lived PyPI token.

## Release trigger

Normal pushes and merges to `master` run CI but do not publish anything. A release starts only when a tag matching `v*` is pushed, and the workflow rejects tags that do not match the version in `pyproject.toml`.

For an agent-managed release, ask `release X.Y.Z`. The repository's `release-draftgoblin` skill covers the complete version PR, merge, tag, publish, and public-install verification workflow.

## One-time setup

1. Sign in to PyPI and create a pending Trusted Publisher with:
   - PyPI project name: `draftgoblin`
   - GitHub owner: `andreagrandi`
   - GitHub repository: `draftgoblin`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`
2. Create a GitHub environment named `pypi`.
3. Configure the `pypi` environment to require manual approval before deployment.
4. Create a fine-grained personal access token for the `andreagrandi/homebrew-tap` repository with read and write access to repository contents.
5. Add that token to the `andreagrandi/draftgoblin` repository as an Actions secret named `HOMEBREW_TAP_TOKEN`.
6. After this change is merged, publish the existing PyPI release to the tap once:

   ```bash
   gh workflow run homebrew.yml \
     --repo andreagrandi/draftgoblin \
     --ref master \
     -f version=<existing-version>
   ```

   Future tagged releases call this workflow automatically.

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
