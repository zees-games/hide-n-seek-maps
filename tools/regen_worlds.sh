#!/bin/bash
# Rebuild the world packs in tools/worlds.tsv from the lossless store through dot-map's regen.
#   [REGEN_IDS="a b"] [REGEN_ARGS=...] tools/regen_worlds.sh     run it through heavy
# Packs and stores are local only (gitignored, never published): see the README's licence notes before shipping one.
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../world"
BSP="${DOTBSP_PATH:-$HERE/../../dot-map/tools/bsp}"
# Inside world/ so a game's res://world link reaches the PBR sets and models by the pack's relative path; Godot skips the dot folder.
STORES="${STORES:-$OUT/.stores}"
rc=0
while IFS=$'\t' read -r id key scale y0 crop; do
    [ -n "$REGEN_IDS" ] && [[ " $REGEN_IDS " != *" $id "* ]] && continue
    d="$OUT/hs_world_$id"; mkdir -p "$d"
    # An optional fifth column: a crop for dot-map's regen, `auto` for the document's boxes (tools/world_crop.py).
    cropargs=()
    [ "$crop" = "auto" ] && crop="$(python3 -I "$HERE/world_crop.py" "$id")"
    [ -n "$crop" ] && cropargs=("--crop=$crop")
    python3 -I "$BSP/bsp.py" regen "$key" --out "$d/$id" --scale "$scale" --y0 "$y0" \
        --pbr-store "$STORES/pbr-library" --model-store "$STORES/model-library" "${cropargs[@]}" $REGEN_ARGS \
        > "${TMPDIR:-/tmp}/regen-$id.log" 2>&1 < /dev/null
    r=$?; [ $r -ne 0 ] && rc=1
    echo "$id exit $r"; tail -3 "${TMPDIR:-/tmp}/regen-$id.log"
done < <(grep -v '^#' "$HERE/worlds.tsv")
exit $rc
