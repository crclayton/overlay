source /home/crclayton/myenv/bin/activate

rm overlayed/overlay_* -rf
#for file in *.jpg *.jpeg *.png *.mp4 *.mov *.MOV; do
#for file in *.jpg *.jpeg *.png *.mp4 *.mov *.MOV .*MP4; do
for file in *_clean*; do #*.MOV .*MP4 *.mp4 *.mov; do
#for file in *.mp4 *clean_.mov *.MOV .*MP4; do
#for file in *; do
  if [[ "$file" == *overlay* ]]; then
    continue  # Skip if "overlay" is in $var
  fi
  python3 embed_metadata_overlay.py "$file" || { echo 'my_command failed' ; exit 1; }
  mv "$file" processed
done

