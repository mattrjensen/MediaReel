import os
import re
import exiftool
from datetime import datetime
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS

SUPPORTED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.heic', '.heif',
    '.mp4', '.mov', '.avi'
}

DATE_SOURCE_METADATA = 'metadata'
DATE_SOURCE_FILENAME = 'filename'
DATE_SOURCE_MODIFIED = 'date modified'
DATE_SOURCE_NONE = 'none'

FILENAME_DATE_PATTERNS = [
    r'(\d{4})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})',
    r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})',
    r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})',
    r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})',
]

ALREADY_FORMATTED = re.compile(r'^\d{8}_\d{6}')


def is_already_formatted(filename: str) -> bool:
    return bool(ALREADY_FORMATTED.match(filename))


def parse_date_from_filename(filename: str):
    stem = Path(filename).stem
    for pattern in FILENAME_DATE_PATTERNS:
        m = re.search(pattern, stem)
        if m:
            try:
                g = m.groups()
                dt = datetime(
                    int(g[0]), int(g[1]), int(g[2]),
                    int(g[3]), int(g[4]), int(g[5])
                )
                return dt
            except ValueError:
                continue
    return None


def strip_date_from_filename(filename: str) -> str:
    path = Path(filename)
    stem = path.stem
    for pattern in FILENAME_DATE_PATTERNS:
        stem = re.sub(pattern, '', stem)
    stem = re.sub(r'^[\s_\-]+|[\s_\-]+$', '', stem)
    if not stem:
        stem = path.stem
    return stem + path.suffix


def read_metadata(filepath: str) -> dict:
    path = Path(filepath)
    ext = path.suffix.lower()
    filename = path.name

    result = {
        'filepath': filepath,
        'filename': filename,
        'ext': ext,
        'is_video': ext in {'.mp4', '.mov', '.avi'},
        'is_already_formatted': is_already_formatted(filename),
        'date': None,
        'date_source': DATE_SOURCE_NONE,
        'stripped_filename': strip_date_from_filename(filename),
    }

    exiftool_path = Path(__file__).parent / 'vendor' / 'exiftool.exe'

    try:
        with exiftool.ExifToolHelper(executable=str(exiftool_path)) as et:
            tags = et.get_metadata(filepath)[0]

            # For video files, check UserData:DateTimeOriginal first —
            # iOS stores this with timezone offset (e.g. 2026:03:28 22:33:36+11:00)
            # which is reliable local time. QuickTime:CreateDate is UTC on iOS.
            candidates = [
                'QuickTime:DateTimeOriginal',  # iOS video — local time with tz offset e.g. 2026:03:28 21:10:31+11:00
                               # Note: exiftool CLI shows this as UserData:DateTimeOriginal but
                               # pyexiftool returns it with group prefix QuickTime:DateTimeOriginal
                'EXIF:DateTimeOriginal',
                'EXIF:CreateDate',
                'QuickTime:CreateDate',
                'QuickTime:MediaCreateDate',
                'XMP:DateTimeOriginal',
                'XMP:CreateDate',
            ]
            for tag in candidates:
                val = tags.get(tag)
                if val and str(val).strip() not in ('', '0000:00:00 00:00:00'):
                    try:
                        dt = datetime.strptime(str(val)[:19], '%Y:%m:%d %H:%M:%S')
                        result['date'] = dt
                        result['date_source'] = DATE_SOURCE_METADATA
                        return result
                    except ValueError:
                        continue
    except Exception:
        pass

    dt = parse_date_from_filename(filename)
    if dt:
        result['date'] = dt
        result['date_source'] = DATE_SOURCE_FILENAME
        return result

    try:
        mtime = os.path.getmtime(filepath)
        result['date'] = datetime.fromtimestamp(mtime)
        result['date_source'] = DATE_SOURCE_MODIFIED
        return result
    except Exception:
        pass

    return result


def build_new_filename(filename: str, dt: datetime, is_interpolated: bool = False,
                       force: bool = False) -> str:
    path = Path(filename)
    date_str = dt.strftime('%Y%m%d_%H%M%S')
    # is_interpolated retained as parameter for future use but no longer
    # affects filename format — amber colour coding in UI signals approximation instead

    if not force and is_already_formatted(filename):
        return filename

    stripped = strip_date_from_filename(filename)
    stripped_path = Path(stripped)
    stem = stripped_path.stem
    ext = stripped_path.suffix if stripped_path.suffix else path.suffix

    if stem:
        return f'{date_str}_{stem}{ext}'
    else:
        return f'{date_str}{ext}'


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: python metadata_reader.py <path_to_file>')
        sys.exit(1)
    result = read_metadata(sys.argv[1])
    print(f"File:        {result['filename']}")
    print(f"Type:        {'video' if result['is_video'] else 'photo'}")
    print(f"Already fmt: {result['is_already_formatted']}")
    print(f"Date:        {result['date']}")
    print(f"Source:      {result['date_source']}")
    print(f"Stripped:    {result['stripped_filename']}")
    if result['date']:
        new_name = build_new_filename(result['filename'], result['date'])
        print(f"New name:    {new_name}")