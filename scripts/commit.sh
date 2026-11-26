#!/bin/sh
# Safe way to commit code from this machine: add ONLY code paths, commit, then pull --rebase (picks up the daily job's commits), then push.
# Commit comes first so the rebase never has unsaved changes to trip over. Never uses git add -A.
# The daily job's folders (docs/, forecast_archive/, predictions/, observations/, model_runs/, corrections/) and data are never added;
# the pre-commit hook also blocks the job folders as a backup.
# Usage: scripts/commit.sh "message"
set -e
cd "$(git rev-parse --show-toplevel)"
[ -n "$1" ] || { echo "Usage: scripts/commit.sh \"message\""; exit 1; }
for p in src scripts tests sites pwa design .github .gitignore requirements*.txt *.md; do
  [ -e "$p" ] && git add -- "$p"
done
if git diff --cached --quiet; then echo "Nothing to commit in code paths."; else
  git commit -m "$1

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
fi
if ! git pull --rebase --autostash; then echo "Pull stopped (conflict). Fix it, then: git rebase --continue (if rebasing) and git push. Your unsaved changes are kept in git stash if they did not re-apply."; exit 1; fi
git push
if [ -n "$(git diff --name-only --diff-filter=U)" ]; then
  echo "WARNING: your unsaved changes to these files clash with the daily job's newer versions (code was committed and pushed fine):"
  git diff --name-only --diff-filter=U
  echo "If they are in docs/, forecast_archive/, predictions/, observations/, model_runs/ or corrections/, the job's version is the right one:"
  echo "  git checkout HEAD -- <those paths> && git stash drop"
fi
