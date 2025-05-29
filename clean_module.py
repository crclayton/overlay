import subprocess, json

import subprocess, json

def probe_tags(path):
    """Returns the format‐level tags (creation_time, location, etc.) as a dict."""
    p = subprocess.run([
            'ffprobe', '-v', 'error',
            '-print_format', 'json',
            '-show_entries', 'format_tags',
            path
        ],
        capture_output=True, text=True
    )
    return json.loads(p.stdout).get('format', {}).get('tags', {})


def get_aspect_ratio(video_path: str) -> str:
    """
    Uses ffprobe to read the video’s width and height,
    then returns the aspect ratio in simplest integer form, e.g. "16:9".
    """
    # Run ffprobe to get stream info in JSON
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-print_format", "json",
        "-show_entries", "stream=width,height",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe error: {result.stderr.strip()}")

    info = json.loads(result.stdout)
    stream = info.get("streams", [{}])[0]
    width = int(stream.get("width", 0))
    height = int(stream.get("height", 0))

    if width == 0 or height == 0:
        raise ValueError("Could not determine video dimensions.")

    # Simplify the ratio
    #divisor = math.gcd(width, height)
    return width, height #f"{width // divisor}:{height // divisor}"


def copy_mov_with_standard_metadata(
    input_mov: str,
    output_mov: str,
    creation_date: str,
    iso6709_location: str,
    rotation: str
) -> None:
    """
    Copy input_mov → output_mov with:
      • video & audio streams untouched
      • all old metadata removed
      • these four format-level tags added back:
         – creation_time
         – com.apple.quicktime.creationdate
         – location
         – com.apple.quicktime.location.ISO6709

    creation_date should look like "2021-11-18T13:51:00-0800"
    iso6709_location should look like "+47.5902-122.2239+005.303/"
    """
    height, width=  get_aspect_ratio(input_mov) # this is fucking backwards for some unknowable reason
    print(width, height, height > width)

    tags = probe_tags(input_mov)
    orig_ct  = tags.get('creation_time') or tags.get('date')     # e.g. "2021-09-30T04:42:36.000000Z"
    orig_loc = tags.get('location')                               # e.g. "+47.5922-122.3330/"

    if orig_ct is None or orig_ct == "None":
        orig_ct = creation_date

    if orig_loc is None or orig_loc == "None":
        orig_loc = iso6709_location

    #print("Original iso", iso6709_location, orig_loc)
    #iso6709_location = tags.get('com.apple.quicktime.location.ISO6709')
    #print("New iso", iso6709_location)

    print(tags)

    cmd = [
        "ffmpeg", "-y",
        "-i", input_mov,
        "-t", "0.25", # TODO FOR DEBUGGING FASTER
        "-map", "0",            # include every stream
        #"-c", "copy",           # no re-encode
        "-map_metadata", "-1", #-1",  # clear all metadata atoms
        "-vf", "transpose=1" if height > width else "transpose=0",
        # add back standardized tags:
        "-metadata", f"creation_time={creation_date}",
        "-metadata", f"com.apple.quicktime.creationdate={creation_date}",
        "-metadata", f"location={iso6709_location}",
        "-metadata", f"com.apple.quicktime.location.ISO6709={iso6709_location}",
        '-metadata', f"date={orig_ct}",                    # if you also care about the “date” field
        '-metadata', f"location={orig_loc}",

        "-metadata:s:v:0", f"rotate={rotation}",
        output_mov
    ]
    subprocess.run(cmd, check=True)

# Example usage:
if __name__ == "__main__":
    copy_mov_with_standard_metadata(
        "IMG_0158.mov",
        "IMG_0158_clean.mov",
        "2021-11-18T13:51:00-0800",
        "+47.5902-122.2239+005.303/"
    )

