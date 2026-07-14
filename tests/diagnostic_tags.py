import exiftool
from pathlib import Path

et_path = str(Path('vendor/exiftool.exe'))
with exiftool.ExifToolHelper(executable=et_path) as et:
    tags = et.get_metadata('D:/Pictures/_Party - Backup/IMG_8754.MP4')[0]
    for k, v in tags.items():
        if 'date' in k.lower() or 'time' in k.lower():
            print(k, '=', v)