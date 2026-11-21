#!/usr/bin/env bash
# One-time publication of this project to GitHub (run in Git Bash from anywhere).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
REPO="derebagtibet/se4458-tuition-assistant"

GH=gh; command -v gh >/dev/null 2>&1 || GH="/c/Program Files/GitHub CLI/gh.exe"
"$GH" --version | head -1
"$GH" auth status >/dev/null 2>&1 || "$GH" auth login --hostname github.com --git-protocol https --web
"$GH" auth setup-git >/dev/null 2>&1 || true

git config user.name  >/dev/null || { echo "Set your git name/email first: git config --global user.name ... / user.email ..."; exit 1; }
[[ -d .git ]] || git init -b main
git add -A

echo "--- safety check"
if git diff --cached --name-only | grep -E '(^|/)\.env$|\.deploy-state$|(^|/)\.venv/' ; then
  echo "Secret or venv file staged - aborting."; exit 1
fi
echo "staged files: $(git diff --cached --name-only | wc -l)"

git commit -q -m "SE 4458 Midterm 1: AI Tuition Assistant (agent, MCP server, REST API, Kong gateway, Azure deployment)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FfC7HG9vVeaoibT488S2ti" || echo "(nothing new to commit)"

if git remote get-url origin >/dev/null 2>&1; then
  git push -u origin main
else
  "$GH" repo create "$REPO" --public --source . --remote origin --push \
    --description "SE 4458 Midterm 1 - AI Tuition Assistant: Gemini agent, MCP policy search, FastAPI + PostgreSQL, Kong gateway, Azure"
fi
echo; echo "Repository: https://github.com/$REPO"
