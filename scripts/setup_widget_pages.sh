#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_SLUG="${REPO_SLUG:-Ara-yjx/stimulize-chatroom-proto}"
BRANCH="${BRANCH:-main}"
BUILD_SCRIPT="scripts/build_pages_site.sh"
WORKFLOW_PATH=".github/workflows/deploy-pages-site.yml"
COMMIT_MESSAGE="${COMMIT_MESSAGE:-Deploy editor and widget to GitHub Pages}"
CUSTOM_DOMAIN="${CUSTOM_DOMAIN:-}"
SKIP_GIT_PUSH="${SKIP_GIT_PUSH:-0}"
SKIP_COMMIT="${SKIP_COMMIT:-0}"
TRIGGER_WORKFLOW="${TRIGGER_WORKFLOW:-1}"
WAIT_FOR_RUN="${WAIT_FOR_RUN:-1}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/setup_widget_pages.sh

Optional environment variables:
  REPO_SLUG           GitHub repo slug, default: Ara-yjx/stimulize-chatroom-proto
  BRANCH              Branch to push and configure for Pages, default: main
  COMMIT_MESSAGE      Commit message for the Pages workflow update
  CUSTOM_DOMAIN       Optional custom domain to bind in GitHub Pages
  SKIP_COMMIT         Set to 1 to skip creating a commit
  SKIP_GIT_PUSH       Set to 1 to skip pushing the branch
  TRIGGER_WORKFLOW    Set to 0 to skip dispatching the Pages workflow
  WAIT_FOR_RUN        Set to 0 to skip waiting for the workflow run

Examples:
  ./scripts/setup_widget_pages.sh
  CUSTOM_DOMAIN=cdn.stimulize.org ./scripts/setup_widget_pages.sh
  SKIP_COMMIT=1 SKIP_GIT_PUSH=1 TRIGGER_WORKFLOW=0 ./scripts/setup_widget_pages.sh
EOF
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

run_git() {
  git -C "$ROOT_DIR" "$@"
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

require_cmd git
require_cmd gh

[[ -x "$ROOT_DIR/$BUILD_SCRIPT" ]] || fail "Build script not found or not executable: $ROOT_DIR/$BUILD_SCRIPT"
[[ -f "$ROOT_DIR/$WORKFLOW_PATH" ]] || fail "Workflow not found: $ROOT_DIR/$WORKFLOW_PATH"

gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated"

echo "Repo: $REPO_SLUG"
echo "Branch: $BRANCH"
echo "Build script: $BUILD_SCRIPT"
if [[ -n "$CUSTOM_DOMAIN" ]]; then
  echo "Custom domain: $CUSTOM_DOMAIN"
else
  echo "Custom domain: <none>"
fi

run_git add "$WORKFLOW_PATH" "$BUILD_SCRIPT" editor frontend

if [[ "$SKIP_COMMIT" != "1" ]]; then
  if ! run_git diff --cached --quiet; then
    run_git commit -m "$COMMIT_MESSAGE"
  else
    echo "No staged changes to commit."
  fi
else
  echo "Skipping commit."
fi

if [[ "$SKIP_GIT_PUSH" != "1" ]]; then
  run_git push origin "$BRANCH"
else
  echo "Skipping git push."
fi

if gh api "repos/$REPO_SLUG/pages" >/dev/null 2>&1; then
  echo "GitHub Pages already enabled."
else
  echo "Enabling GitHub Pages."
  gh api -X POST "repos/$REPO_SLUG/pages" \
    -f build_type=workflow \
    -f source[branch]="$BRANCH" \
    -f source[path]=/
fi

echo "Updating GitHub Pages settings."
if [[ -n "$CUSTOM_DOMAIN" ]]; then
  gh api -X PUT "repos/$REPO_SLUG/pages" \
    -f build_type=workflow \
    -f source[branch]="$BRANCH" \
    -f source[path]=/ \
    -f cname="$CUSTOM_DOMAIN" >/dev/null
else
  gh api -X PUT "repos/$REPO_SLUG/pages" \
    -f build_type=workflow \
    -f source[branch]="$BRANCH" \
    -f source[path]=/ \
    -F cname= >/dev/null
fi

if [[ "$TRIGGER_WORKFLOW" == "1" ]]; then
  echo "Dispatching workflow: Deploy Pages Site"
  gh workflow run "Deploy Pages Site" --repo "$REPO_SLUG"

  if [[ "$WAIT_FOR_RUN" == "1" ]]; then
    sleep 2
    run_id="$(
      gh run list \
        --repo "$REPO_SLUG" \
        --workflow "Deploy Pages Site" \
        --limit 1 \
        --json databaseId \
        --jq '.[0].databaseId'
    )"
    [[ -n "$run_id" && "$run_id" != "null" ]] || fail "Could not find workflow run id"

    echo "Waiting for workflow run $run_id"
    gh run watch "$run_id" --repo "$REPO_SLUG"
  fi
else
  echo "Skipping workflow dispatch."
fi

pages_json="$(gh api "repos/$REPO_SLUG/pages")"
html_url="$(printf '%s' "$pages_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["html_url"])')"
cname="$(printf '%s' "$pages_json" | python3 -c 'import json,sys; value=json.load(sys.stdin)["cname"]; print("" if value is None else value)')"

echo
echo "GitHub Pages URL: $html_url"
if [[ -n "$cname" ]]; then
  echo "Custom domain configured: $cname"
  echo "Remember to create a DNS CNAME pointing $cname to ${REPO_SLUG%%/*}.github.io"
else
  echo "Custom domain configured: <none>"
  echo "Editor URL: $html_url"
  echo "Widget URL: ${html_url}chatroom.min.js"
fi
