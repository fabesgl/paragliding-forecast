#!/bin/sh
# Safe way to commit code from this machine: pull first, then commit and push. Usage: scripts/commit.sh "message"
set -e
git pull --rebase
git add -A
git commit -m "$1

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
