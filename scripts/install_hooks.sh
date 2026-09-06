#!/usr/bin/env bash
# Install the repo's git hooks. Hooks live in .git/hooks, which git does not
# track, so they have to be copied in per clone.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
install -m 755 "$root/scripts/pre-push" "$root/.git/hooks/pre-push"
echo "installed .git/hooks/pre-push (skip it with git push --no-verify)"
