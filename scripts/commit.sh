#!/bin/sh
# Safe way to commit code from this machine: pull --rebase first (picks up the daily job's commits), add ONLY code paths, commit, push.
# Never uses git add -A. The daily job's folders (docs/, forecast_archive/, predictions/, observations/, model_runs/, corrections/)
# and data are never added; the pre-commit hook also blocks the job folders as a backup.
# Usage: scripts/commit.sh "message"
set -e
git pull --rebase --autostash
for p in src scripts tests sites pwa design .github .gitignore CLAUDE.md TODO.md DECISIONS.md README.md *.md requirements*.txt; do
  [ -e "$p" ] && git add -- "$p"
done
git commit -m "$1

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
