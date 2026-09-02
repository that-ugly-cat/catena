#!/usr/bin/env bash
# vendor-translation.sh — fetch the source of Zotero's translation server and
# make it buildable on a host where anonymous git towards GitHub is throttled.
#
# Two obstacles, neither of them in the code.
#
#   1. zotero/translation-server on Docker Hub is published for arm64 only since
#      January 2025 (newest amd64 tag: 2.0.4, from 2021). On an amd64 host the
#      pulled image starts, warns once about the platform, and never answers.
#      So the image is built from source.
#
#   2. That build reaches GitHub over git in three places, and inside a
#      container git has no credentials — so each one goes out anonymously and
#      collects the same 401 that bites `git clone` on this machine:
#
#        - package.json declares one dependency as a git URL;
#        - the Dockerfile clones zotero/translators at build time;
#        - docker-entrypoint.sh runs `git pull` on those translators at every
#          container start.
#
#      All three are rewritten to codeload tarballs: plain HTTPS rather than
#      git, so the throttle does not apply. Pins are preserved rather than
#      floated to a branch.
#
# The consequence worth knowing: translators are then fixed at build time
# instead of refreshing on restart. Re-run this script and rebuild to update
# them — which is a deliberate act rather than a surprise on a Tuesday.
#
# Everything lands in vendor/, which is git-ignored: it survives `git pull` and
# is never committed.
#
#   ./vendor-translation.sh          # into ./vendor/translation-server
#   ./vendor-translation.sh --force  # discard and re-fetch

set -euo pipefail

# python3 on the server, python on a Windows checkout. It is not enough that the
# name resolves: Windows ships a python3.exe stub that exists, prints an advert
# for the Microsoft Store and exits non-zero. So the interpreter is chosen by
# whether it actually runs.
PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
[[ -n "$PY" ]] || { echo "[vendor] needs a working python3 (or python) on PATH" >&2; exit 1; }

REPO="https://github.com/zotero/translation-server.git"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vendor/translation-server"
DEP_NAME="wicked-good-xpath"
DEP_REPO="zotero/wicked-good-xpath"
TRANSLATORS_TARBALL="https://codeload.github.com/zotero/translators/tar.gz/refs/heads/master"

if [[ "${1:-}" == "--force" ]]; then
    echo "[vendor] removing $DEST"
    rm -rf "$DEST"
fi

# --- 0. the source ------------------------------------------------------------

if [[ -d "$DEST/.git" ]]; then
    echo "[vendor] source already present: $DEST"
else
    echo "[vendor] cloning translation-server with its submodules"
    mkdir -p "$(dirname "$DEST")"
    # Submodules are not optional: the Dockerfile does COPY . /app and the code
    # loads modules/translate and modules/utilities at runtime.
    git clone --recurse-submodules --depth=1 "$REPO" "$DEST"
fi

# --- 1. the git dependency in package.json ------------------------------------

PKG="$DEST/package.json"
[[ -f "$PKG" ]] || { echo "[vendor] no package.json in $DEST" >&2; exit 1; }

if grep -q "codeload.github.com/$DEP_REPO" "$PKG"; then
    echo "[vendor] package.json: $DEP_NAME already points at a tarball"
else
    COMMIT="$(grep -o "$DEP_REPO\.git#[0-9a-f]\+" "$PKG" | head -1 | cut -d'#' -f2 || true)"
    if [[ -z "$COMMIT" ]]; then
        echo "[vendor] could not find the pinned commit for $DEP_NAME — leaving" \
             "package.json alone. If npm install fails, that is why." >&2
    else
        TARBALL="https://codeload.github.com/$DEP_REPO/tar.gz/$COMMIT"
        "$PY" - "$PKG" "$DEP_NAME" "$TARBALL" <<'PY'
import json, sys
path, name, url = sys.argv[1:4]
with open(path, encoding="utf-8") as f:
    pkg = json.load(f)
pkg["dependencies"][name] = url
with open(path, "w", encoding="utf-8") as f:
    json.dump(pkg, f, indent=2)
    f.write("\n")
PY
        echo "[vendor] package.json: $DEP_NAME -> tarball of $COMMIT"
    fi
fi

# --- 2. the clone inside the Dockerfile ---------------------------------------

DF="$DEST/Dockerfile"
if grep -q "codeload.github.com/zotero/translators" "$DF"; then
    echo "[vendor] Dockerfile: translators already fetched over HTTPS"
else
    "$PY" - "$DF" "$TRANSLATORS_TARBALL" <<'PY'
import re, sys
path, tarball = sys.argv[1:3]
with open(path, encoding="utf-8") as f:
    text = f.read()
replacement = (
    "# Patched by catena's vendor-translation.sh: a tarball over plain HTTPS\n"
    "# instead of git, because anonymous git to GitHub is throttled from the\n"
    "# host this is built on and the build container carries no credentials.\n"
    "RUN mkdir -p /app/modules/translators \\\n"
    f"    && curl -fsSL {tarball} \\\n"
    "     | tar xz --strip-components=1 -C /app/modules/translators\n"
)
patched, n = re.subn(
    r"^RUN git clone .*?translators\.git.*?$\n", replacement, text,
    count=1, flags=re.M,
)
if not n:
    sys.exit("could not find the translators clone in the Dockerfile")
with open(path, "w", encoding="utf-8") as f:
    f.write(patched)
PY
    echo "[vendor] Dockerfile: git clone of translators -> tarball"
fi

# --- 3. the pull at every container start -------------------------------------

ENTRY="$DEST/docker-entrypoint.sh"
if [[ -f "$ENTRY" ]]; then
    if grep -q "catena" "$ENTRY"; then
        echo "[vendor] entrypoint: already patched"
    else
        "$PY" - "$ENTRY" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    text = f.read()
text = re.sub(
    r"^echo \"-> Updating zotero translators\"\ncd /app/modules/translators/\ngit pull[^\n]*\n",
    "# Patched by catena's vendor-translation.sh. The translators are fixed at\n"
    "# build time here: this pull went out over anonymous git, which the host is\n"
    "# throttled on, and failed noisily at every start. Re-run the script and\n"
    "# rebuild to update them.\n"
    'echo "-> Translators pinned at build time"\n',
    text, count=1, flags=re.M,
)
with open(path, "w", encoding="utf-8") as f:
    f.write(text)
PY
        echo "[vendor] entrypoint: dropped the git pull at startup"
    fi
fi

echo "[vendor] ready. Now:  docker compose up -d --build"
