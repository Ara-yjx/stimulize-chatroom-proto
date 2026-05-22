#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EDITOR_DIR="$ROOT_DIR/editor"
FRONTEND_DIR="$ROOT_DIR/frontend"
PUBLISH_DIR="${1:-$ROOT_DIR/publish}"
BASE_PATH="${VITE_BASE_PATH:-/stimulize-chatroom-proto/}"
export npm_config_cache="${NPM_CONFIG_CACHE:-/tmp/stimulize-chatroom-npm-cache}"
mkdir -p "$npm_config_cache"

echo "[pages] root: $ROOT_DIR"
echo "[pages] publish dir: $PUBLISH_DIR"
echo "[pages] base path: $BASE_PATH"
echo "[pages] npm cache: $npm_config_cache"

rm -rf "$PUBLISH_DIR"
mkdir -p "$PUBLISH_DIR"

echo "[pages] installing frontend deps"
npm --prefix "$FRONTEND_DIR" ci

echo "[pages] building widget"
npm --prefix "$FRONTEND_DIR" run build

echo "[pages] installing editor deps"
npm --prefix "$EDITOR_DIR" ci

echo "[pages] building editor"
VITE_BASE_PATH="$BASE_PATH" npm --prefix "$EDITOR_DIR" run build

echo "[pages] assembling artifact"
cp -R "$EDITOR_DIR/dist/." "$PUBLISH_DIR/"
cp "$FRONTEND_DIR/dist/chatroom.min.js" "$PUBLISH_DIR/chatroom.min.js"
touch "$PUBLISH_DIR/.nojekyll"

echo "[pages] done"
