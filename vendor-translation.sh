#!/usr/bin/env bash
# vendor-translation.sh — fetch the source of Zotero's translation server and
# make it buildable on a host where anonymous git towards GitHub is throttled.
#
# Two problems are solved here, and both are about the network rather than the
# code:
#
#   1. zotero/translation-server on Docker Hub is published for arm64 only since
#      January 2025 (newest amd64 tag: 2.0.4, from 2021). On an amd64 host the
#      pulled image starts, warns once about the platform, and never answers.
#      So the image is built from source.
#
#   2. Its package.json declares one dependency as a git URL. Inside the build
#      container git has no credentials, so `npm install` goes out anonymously
#      and gets a 401 from GitHub — the same throttle that bites `git clone` on
#      this machine. The dependency is rewritten to the codeload tarball of the
#      same pinned commit: plain HTTPS, not git, and therefore unaffected.
#
# Everything lands in vendor/, which is git-ignored: it survives `git pull` and
# is never committed. Re-run it to update the translators.
#
#   ./vendor-translation.sh          # into ./vendor/translation-server
#   ./vendor-translation.sh --force  # discard and re-fetch

set -euo pipefail

REPO="https://github.com/zotero/translation-server.git"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vendor/translation-server"
DEP_NAME="wicked-good-xpath"
DEP_REPO="zotero/wicked-good-xpath"

if [[ "${1:-}" == "--force" ]]; then
    echo "[vendor] removing $DEST"
    rm -rf "$DEST"
fi

if [[ -d "$DEST/.git" ]]; then
    echo "[vendor] already there: $DEST"
else
    echo "[vendor] cloning translation-server with its submodules"
    mkdir -p "$(dirname "$DEST")"
    # Submodules are not optional: the Dockerfile does COPY . /app and the code
    # loads modules/translate and modules/utilities at runtime.
    git clone --recurse-submodules --depth=1 "$REPO" "$DEST"
fi

PKG="$DEST/package.json"
[[ -f "$PKG" ]] || { echo "[vendor] no package.json in $DEST" >&2; exit 1; }

if grep -q "codeload.github.com/$DEP_REPO" "$PKG"; then
    echo "[vendor] $DEP_NAME already points at a tarball"
else
    COMMIT="$(grep -o "$DEP_REPO\.git#[0-9a-f]\+" "$PKG" | head -1 | cut -d'#' -f2 || true)"
    if [[ -z "$COMMIT" ]]; then
        echo "[vendor] could not find the pinned commit for $DEP_NAME — leaving" \
             "package.json alone. If the build fails on npm install, that is why." >&2
    else
        TARBALL="https://codeload.github.com/$DEP_REPO/tar.gz/$COMMIT"
        echo "[vendor] $DEP_NAME: git -> $TARBALL"
        python3 - "$PKG" "$DEP_NAME" "$TARBALL" <<'PY'
import json, sys
path, name, url = sys.argv[1:4]
with open(path, encoding="utf-8") as f:
    pkg = json.load(f)
pkg["dependencies"][name] = url
with open(path, "w", encoding="utf-8") as f:
    json.dump(pkg, f, indent=2)
    f.write("\n")
PY
    fi
fi

echo "[vendor] ready. Now:  docker compose up -d --build"
