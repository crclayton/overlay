#!/usr/bin/env bash
# Pull clips off the Kodak card and concat them in capture order.
#
# The camera writes no embedded date and its MOVI/PICT counter resets, so
# filenames alone neither sort correctly nor stay unique across card dumps.
# Each file is renamed to YYYYMMDD_HHMMSS_<orig>.<ext> using its mtime (which
# the camera does set correctly). process.py parses that prefix as the capture
# date, so clips sort chronologically, and a new MOVI0000 never collides with
# an older MOVI0000.
set -euo pipefail

CARD=/media/crclayton/0403-0201/DCIM

# Move $1 into $2 (a directory) with a timestamp prefix, never overwriting.
import_file() {
    local src=$1 dest_dir=$2
    local name ts target n=1
    name=$(basename "$src")
    ts=$(date -r "$src" +%Y%m%d_%H%M%S)
    target="$dest_dir/${ts}_${name}"
    while [[ -e $target ]]; do
        target="$dest_dir/${ts}_${name%.*}_$n.${name##*.}"
        n=$((n + 1))
    done
    mv -n "$src" "$target"
    [[ -e $src ]] && { echo "✗ failed to move $src (target exists?)" >&2; exit 1; }
    echo "$src -> $target"
    IMPORTED+=("$target")
}

shopt -s nullglob

# Tag any clips already here from a previous dump that predate this scheme.
for f in MOVI*.* PICT*.*; do
    import_file "$f" .
done

# Import new clips from the card, if it's mounted. Only these count as this
# run's upload.
IMPORTED=()
if [[ -d $CARD ]]; then
    for f in "$CARD"/*; do
        [[ -f $f ]] && import_file "$f" .
    done
else
    echo "⚠ card not mounted at $CARD, processing local files only"
fi

# Concat just this run's card files into a dated upload video, e.g.
# overlayed/10-19-2026-upload.mp4. process.py takes a directory, so stage
# symlinks to the new files in a temp dir (concat_raw resolves them).
if (( ${#IMPORTED[@]} )); then
    upload="$(date +%m-%d-%Y)-upload"
    n=1
    name="$upload.mp4"
    while [[ -e overlayed/$name ]]; do
        name="${upload}_$n.mp4"
        n=$((n + 1))
    done
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT
    for f in "${IMPORTED[@]}"; do
        ln -s "$(realpath "$f")" "$stage/"
    done
    echo "Concatenating ${#IMPORTED[@]} new file(s) into overlayed/$name"
    python3 process.py "$stage" overlayed --no-overlay --output "$name"
else
    echo "No new files from the card, skipping upload video"
fi

python3 process.py --no-overlay
