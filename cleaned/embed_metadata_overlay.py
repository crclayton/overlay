# make it so clean also makes the size the same so the overlay stays proportional

import os
import sys
from PIL import Image, ImageDraw, ImageFont
import piexif
import exifread
from moviepy import VideoFileClip, TextClip, CompositeVideoClip, ImageClip
import numpy as np
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from time import sleep
import datetime
from datetime import timedelta

from urllib.request import urlopen
import json
from pytz import timezone


from moviepy.video.tools.subtitles import SubtitlesClip

from hachoir.parser import createParser
from hachoir.metadata import extractMetadata

def split_address(address: str, max_length: int = 25) -> str:
    """
    Break `address` into up to three lines:
      1. Split on first ', ' after max_length
      2. If no comma, split on first space after max_length
      3. If remainder > max_length, repeat the same split on the remainder
    """
    # If it already fits, nothing to do
    if len(address) <= max_length:
        return address

    def _break(s: str) -> (str, str):
        """Try splitting s into (head, tail) at a comma or space."""
        # 1) comma after max_length
        idx = s.find(", ", max_length)
        if idx != -1:
            return s[:idx], s[idx+2:].strip()
        # 2) space after max_length
        idx = s.find(" ", max_length)
        if idx != -1:
            return s[:idx], s[idx+1:].strip()
        # no split point
        return s, ""

    # First split
    line1, rest = _break(address)
    if not rest:
        return line1  # couldn't split at all

    # If the rest is still long, do a second split
    if len(rest) > max_length:
        line2, line3 = _break(rest)
        if line3:
            return f"{line1}\n{line2}\n{line3}"
        else:
            return f"{line1}\n{line2}"
    else:
        return f"{line1}\n{rest}"


#def split_address(address: str, max_length: int = 25) -> str:
#    if len(address) <= max_length:
#        return address
#
#    # Find the first ", " after max_length
#    split_index = address.find(", ", max_length)
#    if split_index == -1:
#        print("No comma to break on, breaking on a space instead")
#        split_index = address.find(" ", max_length)
#        #return address  # No suitable comma found; return original
#
#    # Replace the comma and space with newline
#    return address[:split_index] + "\n" + address[split_index + 2:]


def decimal_coords(coords, ref):
    decimal_degrees = coords[0] + coords[1] / 60 + coords[2] / 3600
    if ref == "S" or ref =='W' :
        decimal_degrees = -decimal_degrees
    return decimal_degrees

def image_coordinates(image_path):

    with open(image_path, 'rb') as src:
        img = Image(src)
    if img.has_exif:
        try:
            img.gps_longitude
            coords = (decimal_coords(img.gps_latitude,
                      img.gps_latitude_ref),
                      decimal_coords(img.gps_longitude,
                      img.gps_longitude_ref))
        except AttributeError:
            print ('No Coordinates')
    else:
        print ('The Image has no EXIF information')


# def extract_image_metadata(image_path):
#     with open(image_path, 'rb') as f:
#         tags = exifread.process_file(f)
#     date = tags.get('EXIF DateTimeOriginal') or tags.get('Image DateTime')
#     print("DATE->",date)
#     if date == None:
#         date_image_path = image_path.replace("Screenshot_","")
#         print(image_path)
#         print(image_path[:15])
#         date = datetime.datetime.strptime(image[:15], "%Y%m%d-%H%M%S")
#         print(date)
#
#     print("DATETIME->",date, type(date))
#
#     gps_lat = tags.get('GPS GPSLatitude')
#     gps_lon = tags.get('GPS GPSLongitude')
#     lat_ref = tags.get('GPS GPSLatitudeRef')
#     lon_ref = tags.get('GPS GPSLongitudeRef')
#
#     def _convert_gps(gps, ref):
#         if gps:
#             d, m, s = [float(str(x).split('/')[0]) for x in gps.values]
#             coord = d + m / 60 + s / 3600
#             if ref in ['S', 'W']:
#                 coord = -coord
#             return coord
#         return None
#
#     lat = _convert_gps(gps_lat, lat_ref)
#     lon = _convert_gps(gps_lon, lon_ref)
#
#     if lat == None or lon == None:
#
#                 # Load EXIF from the file directly
#         exif_dict = piexif.load(image_path)
#
#         # GPS data is under the 'GPS' IFD
#         gps_data = exif_dict.get('GPS')
#         print("GPS", gps_data)
#         if gps_data == {}:
#             print("No GPS data")
#         else:
#             print("GPS_DATA", gps_data)
#             coords = image_coordinates(image_path)
#             print("COORDS", coords)
#             lat = coords["geolocation_lat"]
#             lon = coords["geolocation_lng"]
#             print("LAT, LON", lat, lon)
#
#     print("end", date, lat, lon)
#     return date, lat, lon

def format_pretty_date(date_obj):
    print("OBJ", date_obj)
    if date_obj == None:
        return "0"
    return date_obj.strftime("%b. %d, %Y (%a) ~%-I%p")



#def overlay_text_on_image(image_path, output_path):
#    img = Image.open(image_path).convert("RGBA")
#    date, lat, lon = extract_image_metadata(image_path)
#
#    # Load a nicer font if available
#    try:
#        font = ImageFont.truetype("DejaVuSans.ttf", 40)
#    except:
#        font = ImageFont.load_default()
#
#    print("DATE2", date)
#    pretty_date = format_pretty_date(date)
#    # Prepare the overlay text
#    text = f"Date: {pretty_date or 'Unknown'}\nGPS: {lat or 'N/A'}, {lon or 'N/A'}"
#    text = pretty_date + "\n" + format_pretty_place(lat, lon)
#
#    draw = ImageDraw.Draw(img)
#
#    # Calculate text bounding box (x0, y0, x1, y1)
#    bbox = draw.textbbox((0, 0), text, font=font)
#    text_width = bbox[2] - bbox[0]
#    text_height = bbox[3] - bbox[1]
#
#    # Position: bottom-left with padding
#    padding = 20
#    x = padding
#    y = img.height - text_height - padding
#
#    # Draw semi-transparent background box
#    #bg_box = Image.new("RGBA", (text_width + 2*padding, text_height + 2*padding), (0, 0, 0, 128))
#    #img.paste(bg_box, (x - padding, y - padding), bg_box)
#
#    # Draw text over the background
#    shadow_offset = 3
#    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill="black")
#    draw.text((x, y), text, font=font, fill="#eb9605")
#    #draw.multiline_text((x, y), text, fill="yellow", font=font)
#
#    output_path = "overlay_" + date.strftime("%Y%m%d-%H%M%S") + output_path
#    # Save final image
#    img.convert("RGB").save(output_path)
#    print(f"Saved image with overlay to {output_path}")
#

import subprocess
import json
def get_video_metadata(file_path):
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_entries", "format_tags",
        file_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return json.loads(result.stdout)

import re

def dms_to_decimal(degrees, minutes, seconds, direction):
    decimal = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    if direction in ['S', 'W']:
        decimal *= -1
    return decimal

def extract_decimal_coordinates(text):
    # Regex to match the DMS format
    pattern = re.compile(
        r'(\d+)\s+deg\s+(\d+)\'\s+([\d.]+)"\s+([NS]),\s+'
        r'(\d+)\s+deg\s+(\d+)\'\s+([\d.]+)"\s+([EW])'
    )
    match = pattern.search(text)
    if not match:
        return None, None

    lat_deg, lat_min, lat_sec, lat_dir, lon_deg, lon_min, lon_sec, lon_dir = match.groups()

    latitude = dms_to_decimal(lat_deg, lat_min, lat_sec, lat_dir)
    longitude = dms_to_decimal(lon_deg, lon_min, lon_sec, lon_dir)
    print("IN FUNC", float(latitude), float(longitude))

    return latitude, longitude


def get_gps_from_video(file_path):
    result = subprocess.run(['exiftool', file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in result.stdout.split('\n'):
        if 'GPS' in line or 'Location' in line:
            print(line)
            if "GPS Coordinates" in line:
                print("LINE", line)
                return extract_decimal_coordinates(line)
    return None, None


import datetime, pathlib



def extract_video_metadata(video_path):
    parser = createParser(video_path)
    if not parser:
        return None, None, None

    with parser:
        metadata = extractMetadata(parser)
        if not metadata:
            return None, None, None

        exported = metadata.exportDictionary()
        meta = exported.get('Metadata', {})

        print("METADATA", meta)

        # meta is already a dictionary: keys are strings, values are values
        date = meta.get('Creation date') or meta.get('creation_date')
        date = datetime.datetime.strptime(date, "%Y-%m-%d %H:%M:%S")

        print(date)

        if date == None or date < datetime.datetime(2015,1,1):
            print("Bad date. Trying to extract date from filename", date)
            filename = pathlib.Path(video_path).stem[:8]
            if filename.isdigit():
                try:
                    date = datetime.datetime.strptime(filename, '%Y%m%d')
                except:
                    date = None
            else:
                pass

                # first, try Hachoir parser
                raw = meta.get('Creation date') or meta.get('creation_date')
                date = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S") if raw else None

                # if that failed or is obviously wrong (< 2020), fall back to ffprobe JSON
                if date is None or date < datetime.datetime(2020, 1, 1):
                    print(f"Bad date from Hachoir ({date}); falling back to ffprobe…")
                    info = get_video_metadata(video_path)
                    tags = info.get("format", {}).get("tags", {})

                    # pick the first ISO‐style timestamp available
                    date_str = (
                        tags.get("creation_time")
                        or tags.get("date")
                        or tags.get("com.apple.quicktime.creationdate")
                    )
                    if date_str and date_str != "None":
                        # normalize trailing Z → +00:00 so fromisoformat() will work
                        date = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    else:
                        # last resort: parse YYYYMMDD from the filename stem
                        stem8 = pathlib.Path(video_path).stem[:8]
                        if stem8.isdigit():
                            date = datetime.datetime.strptime(stem8, "%Y%m%d")
                        else:
                            date = datetime.datetime(1970, 1, 1)

            #date = None

        #return date, None, None # DEBUGGING for temp, don't want to use the API that much

        # converting from GMT to pacific (I think)
        date = date - timedelta(hours=8, minutes=0)

        def hour_rounder(t):
            # Rounds to nearest hour by adding a timedelta hour if minute >= 30
            return (t.replace(second=0, microsecond=0, minute=0, hour=t.hour)
                       +timedelta(hours=t.minute//30))

        # round to nearest hour because we just show the %I and otherwise it trunkates 9:55AM to 9AM
        date = hour_rounder(date)

        #date = date.astimezone(timezone('US/Pacific'))

        print("DATE", date)
        lat = meta.get('GPS Latitude') or meta.get('gps_latitude')
        lon = meta.get('GPS Longitude') or meta.get('gps_longitude')

        print("LATLON", lat, lon)
        if lat == None or lon == None:
            metadata = get_video_metadata(video_path)
            print("METADATA2", metadata)

            gps_info = metadata.get("format", {}).get("tags", {})
            print("GPS", gps_info)

            loc = gps_info.get("location")
            print("LOCATION",loc)
            if loc is None or loc == "None":
                print("Getting GPS from video")
                lat, lon = get_gps_from_video(video_path)
            else:
                print("Getting GPS from extracted location string")

                # Remove trailing slash
                loc = loc.rstrip('/')

                print("LOC", loc)

                split = loc.split("-")
                lat = float(split[0])
                lon = float("-" + split[1].split("+")[0])
                print(lat, lon)
                # Find split index: between lat and lon
                # The first character is always + or -
                # The second sign indicates the start of longitude
                #if loc[0] in '+-':
                #    for i in range(1, len(loc)):
                #        if loc[i] in '+-':
                #            lat = float(loc[:i])
                #            lon = float(loc[i:])


        print("LOCATION", lat, lon)

        return date if date else None, lat, lon


# Initialize Nominatim API with a more descriptive user agent
geolocator = Nominatim(user_agent="my_geopy_app")


def exif_to_tag(exif_dict):
    exif_tag_dict = {}
    thumbnail = exif_dict.pop('thumbnail')
    exif_tag_dict['thumbnail'] = thumbnail.decode(codec)

    for ifd in exif_dict:
        exif_tag_dict[ifd] = {}
        for tag in exif_dict[ifd]:
            try:
                element = exif_dict[ifd][tag].decode(codec)

            except AttributeError:
                element = exif_dict[ifd][tag]

            exif_tag_dict[ifd][piexif.TAGS[ifd][tag]["name"]] = element

    return exif_tag_dict

def format_pretty_place(lat, lon):
    if lat == None or lon == None:
        print("No lat or lon")
        return "No GPS Data" #, likely in or near Seattle" #Seattle, Washington"
    #return str(lat) + "," + str(lon)
    try:
        location = geolocator.reverse(str(lat)+","+str(lon))
        address = location.raw['address']
        neighbourhood = address.get('neighbourhood', '')
        city = address.get('city', '')
        town = address.get('town', '')
        borough = address.get('borough', '')
        suburb = address.get('suburb', '')
        state = address.get('state', '')
        shop = address.get('shop', '')
        amenity = address.get('amenity', '') #if not neighbourhood else None
        county = address.get('county', '') if not city else None
        road = address.get('road', '') if not amenity else None
        parts = [shop, amenity, road, neighbourhood, suburb, town, borough, city, county, state]
        clean_address = ", ".join([part for part in parts if part])
        print("LOCATION DATA", location, address, city, state)
        print("Clean address", clean_address)

        split_address_v = split_address(clean_address)
        print("Split address", split_address_v)
        return split_address_v
    except GeocoderTimedOut as e:
        print("GPS geolocator reverse failed")
        sleep(5)
        return "Failed to resolve GPS, likely in or near Seattle" #Seattle, Washington"


def overlay_text_on_video(video_path, output_path):
    date, lat, lon = extract_video_metadata(video_path)
    print("Overlay text start: ", date, lat, lon)
    if date == None: date = datetime.datetime(1970, 1, 1)
    output_path = "overlayed/overlay_" + date.strftime("%Y%m%d-%H%M%S") + output_path

    # Method 1: Using os.path.exists()
    if os.path.exists(output_path):
        print(f"File exists at: {output_path}")
        return
    else:
        print(f"File does not exist at: {output_path} -> creating")

    video = VideoFileClip(video_path)#.subclipped(0,1) #DEBUGGING

    width = video.w
    height = video.h


    print("DATE", date)
    print("LOCATION", lat, lon)

    pretty_date = format_pretty_date(date)
    print(lat, lon)
    location = format_pretty_place(lat, lon)
    shift_up = "\n" in location
    text = pretty_date + "\n" + location


    # Create subtitle image with PIL
    font_size = 40
    font_path = "/usr/share/fonts/truetype/noto/NotoSansMono-Medium.ttf" #"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"  # Adjust for your OS

    # Create a blank image
    img = Image.new("RGBA", (video.w, font_size + 250), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, font_size)

    # Calculate centered text position
    #text_width, text_height = draw.textsize(text, font=font)

    #bbox = draw.textbbox((0, 0),text, font=font)
    #text_width = bbox[2] - bbox[0]
    #text_height = bbox[3] - bbox[1]



    x = 80 #60
    y = 80 #40# if not shift_up else -30 #(video.w - text_width) // 2

    shadow_offset = 3
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill="#eb9605")

    # Convert to numpy and then to ImageClip
    subtitle_img = np.array(img)
    subtitle_clip = ImageClip(subtitle_img, duration=video.duration)



    # rotated to the left
    if "DG_" in video_path: # this is for dualgram (for some reason it's reverse)
        subtitle_clip = subtitle_clip.with_position(("left", "bottom")) # right for bottom
        subtitle_clip = subtitle_clip.rotated(90, expand=True)
    else: # rotated to the right
        subtitle_clip = subtitle_clip.with_position(("right", "top")) # left for bottom
        subtitle_clip = subtitle_clip.rotated(90+180, expand=True)



    #font = ImageFont.load_default()

    #txt_clip = TextClip(
            #font,
        #str(text),
        #fontsize=24,
        #color='white',
        #bg_color='black',
        #method='label'  # <-- THIS IS REQUIRED
    #)

    #txt_clip.set_position(("left", "top"))

    #txt_clip.set_duration(clip.duration)



    video = CompositeVideoClip([video, subtitle_clip])
    #video.with_duration(2)
    video.write_videofile(output_path, codec='libx264')
    #print(f"Saved video with overlay to {output_path}")


def process_media(path):
    if "overlay" in path: return
    ext = os.path.splitext(path)[1].lower()
    #if ext in ['.jpg', '.jpeg', '.png']:
    #    overlay_text_on_image(path, f"overlay_{os.path.basename(path)}")
    if ext in ['.mp4', '.mov']:
        overlay_text_on_video(path, f"overlay_{os.path.basename(path)}")
    else:
        print(f"Unsupported file type: {ext}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python embed_metadata_overlay.py <media_file>")
        sys.exit(1)

    media_file = sys.argv[1]
    print(media_file)
    if not os.path.exists(media_file):
        print("File not found.")
        sys.exit(1)

    process_media(media_file)

