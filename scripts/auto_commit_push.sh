#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: this script must be run from within a git repository." >&2
  exit 1
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
message="Auto-scheduled commit from sorter-combo-smtp"

if [[ $# -ge 1 ]]; then
  message="$1"
fi

if [[ $# -ge 2 ]]; then
  branch="$2"
fi

# Refresh git index to avoid stale file state.
git update-index -q --refresh

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Staging all changes..."
  git add -A
  echo "Creating commit on branch '$branch'..."
  git commit -m "$message"

  if git remote | grep -q '^fork$'; then
    remote='fork'
  else
    remote='origin'
  fi

  echo "Pushing to remote '$remote' on branch '$branch'..."
  git push "$remote" "$branch"
  echo "Push complete."
else
  echo "No changes to commit."
fi
