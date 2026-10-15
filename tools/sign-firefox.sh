#!/bin/sh
# Signs the Firefox extension as an unlisted (self-distributed) add-on, so release Firefox keeps it installed.
# Mozilla signs unlisted add-ons automatically, usually within minutes; nothing is published on addons.mozilla.org.
#
# One-time: create API credentials at https://addons.mozilla.org/developers/addon/api/key/ (needs a free account),
# then:
#   WEB_EXT_API_KEY=user:123:456 WEB_EXT_API_SECRET=... sh tools/sign-firefox.sh
# Install the .xpi it prints from about:addons > gear menu > "Install Add-on From File...".
# Bump "version" in extension/manifest.json and rerun setup before signing again: each signed version is final.
set -eu
SRC="${JAPANESE_WEAVE_HOME:-$HOME/.japanese-weave}/extension-firefox"
OUT="${JAPANESE_WEAVE_HOME:-$HOME/.japanese-weave}/signed"
[ -f "$SRC/manifest.json" ] || { echo "No $SRC; run fw.py setup first." >&2; exit 1; }
: "${WEB_EXT_API_KEY:?set WEB_EXT_API_KEY (see the comment at the top of this script)}"
: "${WEB_EXT_API_SECRET:?set WEB_EXT_API_SECRET}"
mkdir -p "$OUT"
npx --yes web-ext@8 sign --channel=unlisted --source-dir "$SRC" --artifacts-dir "$OUT" \
  --api-key "$WEB_EXT_API_KEY" --api-secret "$WEB_EXT_API_SECRET"
ls -t "$OUT"/*.xpi | head -1
