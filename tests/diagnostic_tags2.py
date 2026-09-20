import exiftool
from pathlib import Path

et_path = str(Path(r'D:\Documents\Work\Projects\MediaReel\vendor\exiftool.exe'))
with exiftool.ExifToolHelper(executable=et_path) as et:
    tags = et.get_metadata(r'D:\Pictures\2026\2026-06-30 - Fiji Family Trip\Saved\20260809_000641_IMG_1305.mov')[0]
    for k, v in tags.items():
        if 'date' in k.lower() or 'time' in k.lower() or 'creation' in k.lower():
            print(k, '=', v)