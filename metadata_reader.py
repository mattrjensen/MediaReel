import os
import re
import sys
import exiftool
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional
from PIL import Image
from PIL.ExifTags import TAGS


def _vendor_path(filename: str) -> str:
    """Resolve a vendor binary path for both source and PyInstaller builds."""
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return str(base / 'vendor' / filename)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0

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


# For video files, check UserData:DateTimeOriginal first — iOS stores this
# with timezone offset (e.g. 2026:03:28 22:33:36+11:00) which is reliable
# local time. QuickTime:CreateDate is UTC on iOS.
DATE_TAG_CANDIDATES = [
    'QuickTime:CreationDate',      # iOS .mov — local time with tz offset e.g. 2026:07:04 14:48:46+12:00
                                   # Note: exiftool CLI shows this as Keys:CreationDate but
                                   # pyexiftool returns it as QuickTime:CreationDate
    'QuickTime:DateTimeOriginal',  # iOS .mp4 — local time with tz offset
                       # Note: exiftool CLI shows as UserData:DateTimeOriginal but
                       # pyexiftool returns as QuickTime:DateTimeOriginal
    'EXIF:DateTimeOriginal',
    'EXIF:CreateDate',
    'QuickTime:CreateDate',
    'QuickTime:MediaCreateDate',
    'XMP:DateTimeOriginal',
    'XMP:CreateDate',
]

# exiftool CLI's `-Duration# -s3` (media_model._get_video_duration, now
# folded into read_metadata_batch) resolves to this tag when read via
# ExifToolHelper.get_metadata() with default common_args — verified against
# sample .mov and .mp4 files.
DURATION_TAG = 'QuickTime:Duration'

METADATA_CHUNK_SIZE = 100

# exiftool's JSON output is UTF-8, but pyexiftool decodes it with the
# platform default (cp1252 on Windows) unless told otherwise. A single
# non-ASCII byte in any file's metadata (lens names, descriptions, maker
# notes) then raised UnicodeDecodeError for the whole call — for a batch,
# the whole 100-file chunk, which fell back to one exiftool launch per file
# (~200ms each); for read_metadata(), that file silently lost its metadata
# date and fell through to filename / date modified.
EXIFTOOL_ENCODING = 'utf-8'


def _new_result(filepath: str) -> dict:
    path = Path(filepath)
    filename = path.name
    return {
        'filepath': filepath,
        'filename': filename,
        'ext': path.suffix.lower(),
        'is_video': path.suffix.lower() in {'.mp4', '.mov', '.avi'},
        'is_already_formatted': is_already_formatted(filename),
        'date': None,
        'date_source': DATE_SOURCE_NONE,
        'stripped_filename': strip_date_from_filename(filename),
    }


def _date_from_tags(tags: dict) -> Optional[datetime]:
    """Walk DATE_TAG_CANDIDATES in priority order, return the first parseable date."""
    for tag in DATE_TAG_CANDIDATES:
        val = tags.get(tag)
        if val and str(val).strip() not in ('', '0000:00:00 00:00:00'):
            try:
                return datetime.strptime(str(val)[:19], '%Y:%m:%d %H:%M:%S')
            except ValueError:
                continue
    return None


def _fallback_date(filepath: str, filename: str):
    """filename date -> date modified -> none. Returns (date, date_source)."""
    dt = parse_date_from_filename(filename)
    if dt:
        return dt, DATE_SOURCE_FILENAME

    try:
        mtime = os.path.getmtime(filepath)
        return datetime.fromtimestamp(mtime), DATE_SOURCE_MODIFIED
    except Exception:
        pass

    return None, DATE_SOURCE_NONE


def _normalize_path(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def read_metadata(filepath: str) -> dict:
    result = _new_result(filepath)
    exiftool_path = _vendor_path('exiftool.exe')

    try:
        with exiftool.ExifToolHelper(executable=str(exiftool_path), encoding=EXIFTOOL_ENCODING) as et:
            tags = et.get_metadata(filepath)[0]
            dt = _date_from_tags(tags)
            if dt:
                result['date'] = dt
                result['date_source'] = DATE_SOURCE_METADATA
                return result
    except Exception:
        pass

    result['date'], result['date_source'] = _fallback_date(filepath, result['filename'])
    return result


def read_metadata_batch(
    filepaths: List[str],
    on_progress: Optional[Callable[[int, int], None]] = None,
    on_chunk: Optional[Callable[[int, List[dict]], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> List[dict]:
    """
    Read metadata for many files using one exiftool process for the whole
    call, in chunks of METADATA_CHUNK_SIZE. Results are returned in the same
    order as filepaths (mapped back by SourceFile, not list position).

    Each result dict has the same keys as read_metadata()'s, plus
    'duration_seconds' (None for non-video, or when a chunk falls back to
    read_metadata() per file).

    Chunking is for progress reporting, cancellation, and limiting the
    damage from one bad chunk — not for command-line length, since pyexiftool
    sends arguments over stdin.

    A file with no output or no usable date tag falls back to filename date,
    then date modified, then none — exactly as read_metadata(). If a chunk
    raises, that chunk is redone with read_metadata() per file.

    If exiftool can't start at all, this raises once — unlike read_metadata(),
    which silently falls back to date-modified for every file.

    on_chunk(start_index, chunk_results), if given, fires after each chunk
    (including a per-file fallback chunk) with that chunk's results in
    filepaths order — lets a caller update rows incrementally without
    waiting for the whole list.
    """
    results: List[Optional[dict]] = [None] * len(filepaths)
    exiftool_path = _vendor_path('exiftool.exe')

    with exiftool.ExifToolHelper(executable=str(exiftool_path), encoding=EXIFTOOL_ENCODING) as et:
        done = 0
        for start in range(0, len(filepaths), METADATA_CHUNK_SIZE):
            if is_cancelled and is_cancelled():
                break

            chunk = filepaths[start:start + METADATA_CHUNK_SIZE]

            try:
                raw_list = et.get_metadata(chunk)
            except Exception:
                fallback_results = [read_metadata(fp) for fp in chunk]
                for i, result in enumerate(fallback_results):
                    results[start + i] = result
                done += len(chunk)
                if on_progress:
                    on_progress(done, len(filepaths))
                if on_chunk:
                    on_chunk(start, fallback_results)
                continue

            by_path = {}
            for tags in raw_list:
                src = tags.get('SourceFile')
                if src:
                    by_path[_normalize_path(src)] = tags

            chunk_results = []
            for i, fp in enumerate(chunk):
                tags = by_path.get(_normalize_path(fp))
                result = _new_result(fp)
                result['duration_seconds'] = None

                if tags is not None:
                    dt = _date_from_tags(tags)
                    if dt:
                        result['date'] = dt
                        result['date_source'] = DATE_SOURCE_METADATA

                    duration = tags.get(DURATION_TAG)
                    if duration is not None:
                        try:
                            result['duration_seconds'] = int(float(duration))
                        except (TypeError, ValueError):
                            pass

                if result['date'] is None:
                    result['date'], result['date_source'] = _fallback_date(fp, result['filename'])

                results[start + i] = result
                chunk_results.append(result)

            done += len(chunk)
            if on_progress:
                on_progress(done, len(filepaths))
            if on_chunk:
                on_chunk(start, chunk_results)

    return results


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