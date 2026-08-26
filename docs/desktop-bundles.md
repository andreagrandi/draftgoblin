# Native desktop bundles

Draft Omen's native desktop bundles are unsigned artifacts built in two
automated contexts: a manual `workflow_dispatch` run for temporary development
testing, or as part of the tagged `v*` release workflow. Ordinary pushes,
merges, and pull requests do not trigger native builds automatically. The macOS
artifact is ad-hoc signed by Nuitka, but it has no developer or distribution
signing identity and is not notarized. The Windows artifact is unsigned.
Neither artifact is a signed installer.

## Tool choice

The repository uses [`pyside6-deploy`](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html), the deployment wrapper maintained with PySide6. It delegates the platform packaging step to Nuitka and has the inputs this application needs: an existing Python entry point, a project file, QML files, Qt modules and plugins, platform icons, and a macOS application bundle.

Briefcase was considered but rejected for this change. Its
[CI packaging workflow](https://briefcase.beeware.org/en/latest/how-to/building/ci/)
uses a stronger native-template and application-layout model and documents
unsigned uploaded artifacts, which would require migrating this existing
PySide6 entry point and launcher conventions. `pyside6-deploy` keeps the
current `draftomen.qt_gui:main` entry point and QML adapter intact while
still producing a `.app` or `.exe`; that smaller migration is the reason for
the choice.

The checked-in platform specs are:

- `pysidedeploy.macos.spec`: `dist-native/macos-unsigned/Draftomen-unsigned-macos.app`
- `pysidedeploy.windows.spec`: `dist-native/windows-unsigned/Draftomen-unsigned-windows.exe`

Both specs pin `Nuitka==4.1.3`. The workflow installs that exact deployment
dependency before invoking `pyside6-deploy`; the existing `uv.lock` continues
to select the PySide6 version. Run deployment commands from the repository root
because the specs use repository-relative paths.

## Explicit bundle inputs

The `[tool.pyside6-project]` table in `pyproject.toml` enumerates the Python
sources, every QML file, `qml/qmldir`, the logo, and both platform icons. Each
spec repeats the QML list in its supported `[qt] qml_files` key and declares the
Qt inputs used by the adapter:

- modules: `Core`, `Gui`, `Qml`, `Quick`, and `QuickControls2`;
- plugins: `imageformats`, `platforms`, `platformthemes`, and `styles`;
- QML: `QtQuick`, `QtQuick.Controls`, and `QtQuick.Layouts` imports through the
  `draftomen/qml` module;
- resources: `draftomen/assets/draftomen_logo.png` plus the `.icns` and `.ico`
  icons generated from that checked-in logo. These native icon files are
  checked-in source inputs, not CI build products; regenerate them from the
  logo with deterministic fixed-size PNG variants when branding changes.

Both specs keep PySide6's default unused QML plugin exclusions explicit:
`QtCharts`, `QtQuick3D`, `QtSensors`, `QtTest`, and `QtWebEngine`.

`NavigationRail.qml` resolves the logo relative to the bundled QML directory.
The pyside6-deploy QML data-directory input preserves the QML and assets
siblings, and `draftomen.qt_gui._qml_directory()` also falls back to a
`qml` directory beside a compiled executable when the source package path is
not present. Card preview images are cache data and are intentionally not
bundle resources.

No application fonts are bundled. The GUI deliberately uses Qt's system fixed
font, so a bundle does not capture machine-specific font files. Application
metadata remains canonical (`Draft Omen` and the package version) through
`qt_gui._configure_application_metadata`; the platform spec title and output
directory mark each result as an unsigned development artifact. On macOS,
“unsigned” means no developer/distribution identity or notarization despite
Nuitka's required ad-hoc signature.

## Local build and inspection

Install the locked GUI dependencies and the pinned deployment dependency:

```bash
uv sync --locked --extra gui
uv pip install "Nuitka==4.1.3"
```

Build the platform matching the host:

```bash
# macOS
mkdir -p dist-native/macos-unsigned
uv run pyside6-deploy --config-file pysidedeploy.macos.spec --force

# Windows PowerShell
New-Item -ItemType Directory -Force dist-native/windows-unsigned | Out-Null
uv run pyside6-deploy --config-file pysidedeploy.windows.spec --force
```

The deployment output directory must exist before `pyside6-deploy` finalizes
the bundle. The native workflow creates the matrix platform's directory
explicitly; the local commands above do the same.

Standalone Windows builds require Dependency Walker. The Windows workflow
downloads the official x64 2.2 archive over HTTPS, verifies its pinned SHA-256
(`35db68a613874a2e8c1422eb0ea7861f825fc71717d46dabf1f249ce9634b4f1`),
and extracts it into Nuitka's downloads cache before packaging. A changed
archive fails the build instead of executing unverified bytes. A first local
Windows build may still prompt before populating the same user cache.

The expected deployment output is the platform-specific path listed above.
For a non-building input inspection, add `--dry-run`; pyside6-deploy should
report the checked QML list and a generated Nuitka command containing the
PySide6 plugin, the QML/assets data directories, the platform icon option, and
the declared Qt module/plugin inputs. `--dry-run` does not produce a runnable
bundle.

The deterministic smoke helper launches the actual compiled executable. It
does not import or run the source GUI, and it removes developer Python path and
virtual-environment overrides before launching the bundle:

```bash
# macOS
uv run python tests/bundle_smoke.py \
  dist-native/macos-unsigned/Draftomen-unsigned-macos.app

# Windows PowerShell
uv run python tests/bundle_smoke.py `
  --timeout 300 `
  dist-native/windows-unsigned/Draftomen-unsigned-windows.exe
```

The helper reads `Contents/Info.plist` and resolves the macOS executable named
by `CFBundleExecutable`, so neighboring binaries and libraries do not affect
selection. It uses the Windows `.exe` directly, then passes `--provider mock
--smoke-test` and an isolated temporary `--app-dir`. A successful smoke run
exits with code 0 after the existing deterministic 800 ms GUI smoke behavior.
The helper defaults to a 60-second process timeout. The Windows CI smoke run
allows 300 seconds because the first launch must unpack the compressed one-file
runtime before the application's smoke timer starts; the bounded timeout still
fails a bundle that does not exit.
Mock mode avoids network, Arena logs, card downloads, and machine-specific
runtime caches, so the bundle can be visually inspected without live services.

## GitHub Actions artifacts and tagged releases

`.github/workflows/native-bundles.yml` has two entry points:

- `workflow_dispatch` lets a developer start a temporary development build
  from the Actions page. Its outputs remain GitHub Actions run artifacts.
- `workflow_call` lets the tagged release workflow invoke the same build and
  smoke-test implementation. There is no automatic `pull_request` trigger,
  and ordinary pushes or merges do not trigger native builds.

The workflow runs separate `macos-latest` and `windows-latest` jobs. It syncs
the locked GUI environment, installs Nuitka 4.1.3, builds with the platform
spec, and smoke-tests the same payload shape that it uploads:

- macOS: the completed `Draftomen-unsigned-macos.app` is placed in a
  `Draftomen-unsigned-macos.tar` archive. The workflow extracts that tar into
  an isolated temporary directory and runs `tests/bundle_smoke.py` against the
  extracted app before uploading the tar.
- Windows: the workflow runs `tests/bundle_smoke.py` directly against
  `Draftomen-unsigned-windows.exe` and uploads that `.exe` directly.

### Manual development artifacts

The uploaded artifact names are:

- `draftomen-macos-unsigned-development`;
- `draftomen-windows-unsigned-development`.

Download these from the **Actions** page: open the manual workflow run and
download its artifacts from the run summary. They are GitHub Actions run
artifacts, not GitHub Release assets; they are retained only for the
repository's configured Actions artifact-retention period and may expire.

The macOS artifact download is a GitHub Actions artifact archive containing
exactly one file, `Draftomen-unsigned-macos.tar`; it is not the `.app`
directory directly. Extract that downloaded artifact archive first, then
extract the tar into an empty directory. The tar contains exactly one
top-level directory, `Draftomen-unsigned-macos.app`, including its
`Contents/` tree and executable mode bits. For example:

```bash
unzip draftomen-macos-unsigned-development.zip -d macos-download
mkdir macos-extracted
tar -xpf macos-download/Draftomen-unsigned-macos.tar -C macos-extracted
uv run python tests/bundle_smoke.py \
  macos-extracted/Draftomen-unsigned-macos.app
```

The Windows artifact download contains the direct
`Draftomen-unsigned-windows.exe` file:

```powershell
Expand-Archive draftomen-windows-unsigned-development.zip windows-download
uv run python tests/bundle_smoke.py `
  windows-download/Draftomen-unsigned-windows.exe
```

The macOS tar is only a mode-preserving transport envelope; it is not signed
or notarized. The app inside it has Nuitka's required ad-hoc signature, but no
developer or distribution signing identity and no notarization. The Windows
executable is unsigned. Anyone distributing either downloaded artifact must
perform platform-appropriate signing and notarization independently after
extracting the macOS app from the tar.

### Tagged release assets

For a tag such as `v1.2.3`, `release.yml` invokes the reusable native workflow
with `workflow_call`. The GitHub Release publication job runs only after both
the existing `publish` job has successfully published the Python distributions
to PyPI and both native bundle jobs have built and passed their smoke tests.
It checks out the tagged repository, extracts the non-empty body under the exact
`## [1.2.3] - YYYY-MM-DD` section in `CHANGELOG.md`, and uses that body as the
GitHub Release notes. A missing, duplicate, or empty section fails the job
before release publication. The job downloads the two native Actions artifacts
from that release run, renames their payloads, generates SHA-256 checksums, and
creates or updates the GitHub Release with those notes.

Release publication is recoverable: rerunning the job reuses an existing draft
or release, replaces the three assets and changelog notes, and publishes any
draft left by an earlier interrupted attempt. Native Actions artifacts are
likewise overwritten when their build jobs are rerun.

The persistent public assets attached to the `v1.2.3` GitHub Release are:

- `draftomen-v1.2.3-unsigned-macos.tar`, containing the
  `Draftomen-unsigned-macos.app` bundle;
- `draftomen-v1.2.3-unsigned-windows.exe`, containing the Windows
  executable; and
- `draftomen-v1.2.3-unsigned-sha256sums.txt`, containing SHA-256 entries
  for those two assets.

The release filenames deliberately include both the tag and `unsigned`.
The macOS asset remains a tar transport so executable mode bits survive
download. It is not developer/distribution signed or notarized; its app has
only Nuitka's required ad-hoc signature. The Windows executable has no
distribution signature. These GitHub Release assets therefore require
platform-appropriate signing and notarization before redistribution.

The existing Python `publish` job and Homebrew job remain separate from native
packaging: Python wheels and source distributions continue to publish to
PyPI, and Homebrew continues to run after `publish` using its existing
workflow. The GitHub Release adds persistent native assets; it does not replace
the PyPI or Homebrew publication paths.
