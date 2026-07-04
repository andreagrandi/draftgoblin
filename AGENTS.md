## Session Start Workflow

Before making code or documentation changes in this repo:

1. Switch back to `master`.
2. Pull the latest changes with `git pull --ff-only`.
3. Create a new branch with a short, descriptive name related to the feature being added or the bug being fixed.
4. Make the requested changes on that branch.

Do not start work from an old feature branch unless the user explicitly asks to continue that branch.

## Verification Requirement

Before claiming any implementation is done, perform end-to-end verification of the actual user-facing workflow, not just unit tests or isolated helpers. If true end-to-end verification is impossible in the current environment, say so explicitly, document what was verified instead, and do not present the work as complete.

