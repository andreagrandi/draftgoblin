---
name: development-release
description: >-
  Build and publish the rolling GitHub prerelease for a native Draftgoblin
  development build. Use when the user says "make a new dev release" or asks
  for a new development/native prerelease build.
---

# Make a development release

An explicit request to make a new dev release authorizes the remote workflow
dispatch and release verification below, but not commits, pushes, stable
publication, PyPI publication, or Homebrew publication. Do not perform any of
those actions.

Use `gh` for every GitHub operation. Set the repository once:
```sh
repo=andreagrandi/draftgoblin
```
## Preflight and dispatch
1. Run `gh auth status` and stop if authentication is unavailable.
2. Verify the workflow exists on `master` without dispatching it:

   ```sh
   gh workflow view native-bundles.yml --repo "$repo" --ref master --yaml
   ```

3. Create one unique, UTC request ID. Keep it for the entire operation:

   ```sh
   request_id="dev-$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 6)"
   display_title="Development release request $request_id"
   ```
4. Dispatch exactly once, on `master`, passing that input:

   ```sh
   gh workflow run native-bundles.yml --repo "$repo" --ref master \
     -f request_id="$request_id"
   ```
Never dispatch a second time, whether run lookup is delayed or the build
fails.

## Locate and watch the requested run

GitHub may take a few seconds to list a dispatched run. Poll the run list for a
bounded period, matching the **exact** display title; never assume the latest
run is yours:

```sh
run_id=""
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  run_id=$(gh run list --repo "$repo" --workflow native-bundles.yml \
    --branch master --event workflow_dispatch --limit 100 \
    --json databaseId,displayTitle \
    --jq "map(select(.displayTitle == \"$display_title\"))[0].databaseId // empty")
  [ -n "$run_id" ] && break
  sleep 3
done
[ -n "$run_id" ] || { echo "Could not locate $display_title; not redispatching." >&2; exit 1; }

if ! gh run watch "$run_id" --repo "$repo" --exit-status; then
  gh run view "$run_id" --repo "$repo" --log-failed || true
  exit 1
fi
```
## Verify the rolling prerelease
Read the exact run metadata and package version from its source commit:
```sh
run_json=$(gh run view "$run_id" --repo "$repo" --json number,headSha,url)
run_number=$(printf '%s' "$run_json" | jq -r .number)
head_sha=$(printf '%s' "$run_json" | jq -r .headSha)
workflow_url=$(printf '%s' "$run_json" | jq -r .url)
version=$(gh api "repos/$repo/contents/pyproject.toml?ref=$head_sha" --jq '.content | @base64d' | awk -F'"' '/^version[[:space:]]*=/ { version=$2 } END { print version }')
release_json=$(gh release view development --repo "$repo" --json tagName,isPrerelease,name,body,assets,url)
release_url=$(printf '%s' "$release_json" | jq -r .url)
release_title=$(printf '%s' "$release_json" | jq -r .name)
release_date=$(printf '%s' "$release_title" | jq -Rr 'capture("^Draftgoblin v.+ development build (?<date>[0-9]{4}-[0-9]{2}-[0-9]{2}) #[0-9]+$") | .date')
[ -n "$release_date" ] || { echo "Release title has no YYYY-MM-DD date." >&2; exit 1; }
utc_date=${release_date//-/}; build_id="v${version}-dev.${utc_date}.${run_number}"
expected_title="Draftgoblin v${version} development build ${release_date} #${run_number}"
if ! printf '%s' "$release_json" | jq -e \
  --arg title "$expected_title" --arg build "$build_id" \
  --arg workflow "$workflow_url" --arg run "$run_id" --arg commit "$head_sha" \
  --arg macos "draftgoblin-${build_id}-unsigned-macos.tar" \
  --arg windows "draftgoblin-${build_id}-unsigned-windows.exe" \
  --arg checksums "draftgoblin-${build_id}-unsigned-sha256sums.txt" \
  '(.tagName == "development") and (.isPrerelease == true) and
   (.name == $title) and ((.body // "") | contains($build)) and
   ((.body // "") | contains($workflow)) and ((.body // "") | contains($run)) and ((.body // "") | contains($commit)) and
   (([.assets[].name] | sort) == ([$macos, $windows, $checksums] | sort)) and
   all(.assets[]; (.size > 0) and ((.url // "") | length > 0))'
then
  echo "Rolling development release metadata or assets failed verification." >&2
  exit 1
fi
```

Report the exact build ID, workflow URL, and rolling release URL. If any
verification fails, report the failure and do not dispatch another run.
