#!/usr/bin/env python3
import os
import sys
import subprocess
import signal

# Video extensions to process
VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.flv', '.wmv'}

def is_video_file(fname):
    return os.path.splitext(fname)[1].lower() in VIDEO_EXTS

def rotate_filter(choice):
    return {
        '1': 'transpose=2',           # 90° left
        '2': 'transpose=1',           # 90° right
        '3': 'transpose=2,transpose=2'  # 180°
    }.get(choice)

def process_file(path):
    print(f"\n▶ Now previewing: {os.path.basename(path)}")
    print("  (Type 0=no change, 1=90° left, 2=90° right, 3=180°, then Enter)")

    # launch ffplay in the background
    player = subprocess.Popen(
        #['ffplay', '-autoexit', '-loglevel', 'error', path],
        #stdout=subprocess.DEVNULL,
        #stderr=subprocess.DEVNULL
        ['mpv', '--no-audio', '--start=50%', '--frames=1', '--keep-open', path, '--autofit=640x360', '--no-terminal'],
    )

    # get user choice
    choice = ''
    while choice not in {'0','1','2','3'}:
        choice = input("Your choice [0–3]: ").strip()

    # kill the player immediately
    try:
        player.send_signal(signal.SIGINT)
    except Exception:
        player.kill()
    player.wait()

    if choice == '0':
        print("↩ Skipped, no rotation.\n")
        return

    vf = rotate_filter(choice)
    base, ext = os.path.splitext(path)
    out = f"{base}_rot{choice}{ext}"
    #cmd = [
    #    'ffmpeg', '-i', path,
    #    '-vf', vf,
    #    '-c:a', 'copy',
    #    out
    #]
    cmd = [
        'ffmpeg',
        '-i', path,
        # copy all input metadata (creation_time, location, etc.) into the output:
        '-map_metadata', '0',
        # apply your transpose filter
        '-vf', vf,
        # re-encode video; copy audio
        '-c:v', 'libx264',
        '-c:a', 'copy',
        # clear out the old rotate tag so it’s not applied on top of the filtered pixels:
        '-metadata:s:v:0', 'rotate=0',
        out
    ]



    print(f"⟳ Rotating → {os.path.basename(out)}")
    subprocess.run(cmd, check=True)
    cmd = [
        'mv', path, "unrotated"
    ]
    print(f"⟳ Moving {path} to unrotated directory")
    subprocess.run(cmd, check=True)


    print("✅ Done.\n")

def main(directory):
    for root, _, files in os.walk(directory):
        for f in sorted(files):
            if is_video_file(f):
                process_file(os.path.join(root, f))
        break # don't search recursively

if __name__ == '__main__':
    dir_to_scan = sys.argv[1] if len(sys.argv) > 1 else '.'
    main(dir_to_scan)

