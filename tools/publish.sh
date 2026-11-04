#!/usr/bin/env bash
# Publishes this pack to the CDN. world/ (hs_world_*) is derived from the source authors' faces and lightmaps
# and is never published, so the folder is excluded by name; the maps/ documents and provides.json go.
# Publish from a clean checkout of pushed HEAD. Dev: DOT_SITE=https://tmcdev.net with ~/.config/dot/creator-token-dev; prod: DOT_SITE=https://moddingcommunity.com with creator-token-prod.
#   heavy -- env DOT_SITE=https://tmcdev.net tools/publish.sh --version 1.0.0 --token-file ~/.config/dot/creator-token-dev --lock <lock> [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUB="${DOT_CLOUD_PATH:-$ROOT/../dot-cloud}/tools/publish_pack.sh"
[ -x "$PUB" ] || { echo "publish.sh: dot-cloud not found beside this repository (set DOT_CLOUD_PATH)" >&2; exit 1; }
exec "$PUB" --source "$ROOT" --id hide-n-seek-maps --exclude-dir world --exclude-dir tools --lock "$ROOT/content.lock.json" "$@"
