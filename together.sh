rm temp_clips/*
rm concat_list.txt
mkdir temp_clips
i=0
for f in $(ls | sort | grep overlay); do
  printf -v fname "temp_clips/out_%03d.mp4" "$i"
  ffmpeg -y -i "$f" -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"  -r 30 -preset fast "$fname"
  i=$((i+1))
done
for f in temp_clips/*.mp4; do echo "file '$f'" >> concat_list.txt; done
ffmpeg -f concat -safe 0 -i concat_list.txt -c copy final_output.mp4 -y

