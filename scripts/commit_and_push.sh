#!/usr/bin/env bash
# Commit the given paths if they changed, then push. Used by the daily GitHub Actions workflow.
#
#   bash scripts/commit_and_push.sh "<commit message>" <path> [<path> ...]
#
# - Nothing changed -> no commit, exit 0 (a same-day rerun is a no-op, see DECISIONS D9).
# - Push rejected because the remote moved (e.g. someone pushed code meanwhile) -> rebase on it and retry.
set -euo pipefail

message="$1"
shift

git add -- "$@"
if git diff --cached --quiet; then
  echo "Nothing changed in: $*"
  exit 0
fi

git commit --quiet -m "$message"
branch="$(git rev-parse --abbrev-ref HEAD)"

for attempt in 1 2 3; do
  if git push --quiet origin "HEAD:${branch}"; then
    echo "Pushed: $message"
    exit 0
  fi
  echo "Push rejected (attempt ${attempt}/3): rebasing on origin/${branch} and retrying"
  sleep $((attempt * 5))
  # --autostash: other uncommitted files (e.g. outputs of later steps) are set aside during the rebase.
  git pull --quiet --rebase --autostash origin "${branch}"
done

echo "Push failed after 3 attempts" >&2
exit 1
