#!/bin/sh
# Safe way to commit code from this machine: commit, then pull --rebase (picks up the daily job's commits), then push.
# Usage: scripts/commit.sh "message"
set -e
git pull --rebase --autostash
git add -A
git commit -m "$1

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
