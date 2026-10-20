#!/bin/sh
# Rasterizes tools/icons/icon.svg into the extension's icons (extension/icons/icon-<size>.png, for the toolbar, the
# add-ons page and stores), the grey icon the toolbar shows where a page gets no Japanese (off-<size>.png) by swapping
# the SVG's three colors for greys, and the faded icon it shows where a page has nothing to weave (faded-<size>.png) by
# drawing the same colors at 40% opacity (see extension/src/toolbar.js). Rerun after changing the SVG and commit the
# PNGs with it. Needs rsvg-convert (librsvg); without one on PATH it runs itself again in nix shell nixpkgs#librsvg.
#   sh tools/make-icons.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
command -v rsvg-convert >/dev/null 2>&1 || exec nix shell nixpkgs#librsvg -c sh "$here/make-icons.sh"
src="$here/icons/icon.svg"
out="$here/../extension/icons"
mkdir -p "$out"
grey() { sed -e 's/#233067/#6b7280/g' -e 's/#e2452f/#a3a8b1/g' -e 's/#f6ead2/#e5e7eb/g' "$src"; }
faded() { sed -e 's|^\(<svg [^>]*>\)$|\1<g opacity="0.4">|' -e 's|^</svg>$|</g></svg>|' "$src"; }
for size in 16 32 48 96 128; do rsvg-convert -w "$size" -h "$size" -o "$out/icon-$size.png" "$src"; done
for size in 16 32 48; do grey | rsvg-convert -w "$size" -h "$size" -o "$out/off-$size.png"; done
for size in 16 32 48; do faded | rsvg-convert -w "$size" -h "$size" -o "$out/faded-$size.png"; done
ls "$out"
