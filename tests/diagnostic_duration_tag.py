"""
One-off: find which exiftool tag key (as returned by ExifToolHelper.get_metadata,
using default common_args ["-G", "-n"]) matches the value produced today by
`exiftool -Duration# -s3 <file>` (media_model._get_video_duration), so batch
mode can pull duration from the same get_metadata() call instead of a second
subprocess.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import exiftool
from metadata_reader import _vendor_path

if len(sys.argv) < 2:
    print(__doc__)
    print('Usage: python tests/diagnostic_duration_tag.py <folder>')
    sys.exit(1)
folder = Path(sys.argv[1])
videos = sorted([str(f) for f in folder.iterdir() if f.suffix.lower() in ('.mp4', '.mov')])[:6]

exiftool_path = _vendor_path('exiftool.exe')

for fp in videos:
    cli = subprocess.run(
        [exiftool_path, '-Duration#', '-s3', fp],
        capture_output=True, text=True, timeout=10,
    )
    cli_val = cli.stdout.strip()

    with exiftool.ExifToolHelper(executable=exiftool_path) as et:
        tags = et.get_metadata(fp)[0]
    duration_tags = {k: v for k, v in tags.items() if 'duration' in k.lower()}

    print(Path(fp).name)
    print('  CLI -Duration# -s3 :', repr(cli_val))
    for k, v in duration_tags.items():
        match = '  <-- MATCH' if str(v) == cli_val else ''
        print(f'  {k} = {v}{match}')
    print()
