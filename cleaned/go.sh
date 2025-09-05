source /home/crclayton/myenv/bin/activate

#rm overlayed/overlay_* -rf
#for file in *.jpg *.jpeg *.png *.mp4 *.mov *.MOV; do
#for file in *.jpg *.jpeg *.png *.mp4 *.mov *.MOV .*MP4; do
for file in *_clean*; do
    if [[ "$file" == *overlay* ]]; then
        continue # Skip if "overlay" is in $file
    fi

    if python3 embed_metadata_overlay.py "$file"; then
        mv "$file" processed
    else
        echo 'embed_metadata_overlay.py failed or was interrupted'
        exit 1
    fi
done
