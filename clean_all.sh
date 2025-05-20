#!/usr/bin/env bash
shopt -s nullglob

rm *_clean.mov

clean_mov_with_standard_metadata() {
  local inp="$1" out="$2" creation_date="$3" location="$4" rotation="$5"
  python3 - "$inp" "$out" "$creation_date" "$location" "$rotation" << 'EOF'
import sys
from clean_module import copy_mov_with_standard_metadata
copy_mov_with_standard_metadata(
    sys.argv[1],  # input_mov
    sys.argv[2],  # output_mov
    sys.argv[3],  # creation_date
    sys.argv[4],  # iso6709_location
    sys.argv[5]   # rotation
)
EOF
}

for f in *.mp4 *.mov *.MOV *.MP4; do
  # 1) Read creation & location
  creation_date=$(ffprobe -v quiet \
    -show_entries format_tags=com.apple.quicktime.creationdate \
    -of default=nw=1:nk=1 "$f")

  location=$(ffprobe -v quiet \
    -show_entries format_tags=com.apple.quicktime.location.ISO6709 \
    -of default=nw=1:nk=1 "$f")

  # 2) Read rotation (defaults to 0 if unset)
  rotation=$(ffprobe -v quiet \
    -select_streams v:0 \
    -show_entries stream_tags=rotate \
    -of default=nw=1:nk=1 "$f")
  rotation=${rotation:-0}

  if [[ -z $creation_date || -z $location ]]; then
    echo "⚠️  Skipping $f: missing tags"
    continue
  fi

  out="${f%.*}_clean.mov"
  clean_mov_with_standard_metadata "$f" "$out" \
    "$creation_date" "$location" "$rotation" \
    && echo "✔  $f → $out"
  mv $f cleaned/
  #exit
done


