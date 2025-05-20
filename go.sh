rm temp_clips/*
rm final_output.mp4
rm overlay* -rf
#for file in *.jpg *.jpeg *.png *.mp4 *.mov *.MOV; do
#for file in *.jpg *.jpeg *.png *.mp4 *.mov *.MOV .*MP4; do
#for file in *.mp4 *.mov *.MOV .*MP4; do
#for file in *.mp4 *clean_.mov *.MOV .*MP4; do
for file in *_clean*; do
  if [[ "$file" == *overlay* ]]; then
    continue  # Skip if "overlay" is in $var
  fi
  python3 embed_metadata_overlay.py "$file"
done
mpv overlay*


