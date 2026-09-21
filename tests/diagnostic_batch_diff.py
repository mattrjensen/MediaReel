"""
Compare the original one-process-per-file read_metadata() against the current
read_metadata() and read_metadata_batch() over the same real folder. Any
difference in date / date_source / proposed filename is a bug unless
explained.

Not a pytest test — run directly:

    python tests/diagnostic_batch_diff.py <folder> [original_metadata_reader.py]

The original implementation is taken from the last commit before batching
(ORIGINAL_COMMIT) via `git show`, unless a path to a copy is given.

Expected differences: the original decoded exiftool output as cp1252, so a
file with non-ASCII metadata silently lost its metadata date and fell back to
its filename date; the current code decodes as UTF-8. Those files show the
same date but a different date_source ('filename' -> 'metadata').
"""
import importlib.util
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Last commit before batched metadata reading was introduced.
ORIGINAL_COMMIT = '216042b'


def load_original_module(snapshot_path=None):
    """Import the pre-batching metadata_reader as a separate module."""
    if snapshot_path is None:
        source = subprocess.run(
            ['git', 'show', f'{ORIGINAL_COMMIT}:metadata_reader.py'],
            cwd=PROJECT_ROOT, capture_output=True, encoding='utf-8', check=True,
        ).stdout
        tmp = tempfile.NamedTemporaryFile(
            'w', suffix='.py', delete=False, encoding='utf-8')
        tmp.write(source)
        tmp.close()
        snapshot_path = tmp.name

    spec = importlib.util.spec_from_file_location('metadata_reader_old', snapshot_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


import metadata_reader as new_mr
from metadata_reader import build_new_filename, SUPPORTED_EXTENSIONS

old_mr = load_original_module(sys.argv[2] if len(sys.argv) > 2 else None)

# The original module's _vendor_path() resolves relative to its own
# __file__ (a temp file), not the project root — so it would never find the
# real vendor/exiftool.exe and would silently fall back to filename parsing
# for every file. Point it at the real resolver instead.
old_mr._vendor_path = new_mr._vendor_path


def collect_files(folder: str):
    p = Path(folder)
    return sorted([
        str(f) for f in p.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    folder = sys.argv[1]
    filepaths = collect_files(folder)
    print(f'Folder: {folder}')
    print(f'Files:  {len(filepaths)}')
    print()

    print('--- Old read_metadata() (sequential) ---')
    t0 = time.perf_counter()
    old_results = [old_mr.read_metadata(fp) for fp in filepaths]
    t_old = time.perf_counter() - t0
    print(f'{t_old:.2f}s')

    print('--- New read_metadata_batch() ---')
    t0 = time.perf_counter()
    new_results = new_mr.read_metadata_batch(filepaths)
    t_new = time.perf_counter() - t0
    print(f'{t_new:.2f}s  ({t_old / t_new:.1f}x faster than old sequential)')
    print()

    mismatches = []
    for old, new, fp in zip(old_results, new_results, filepaths):
        old_name = build_new_filename(old['filename'], old['date']) if old['date'] else None
        new_name = build_new_filename(new['filename'], new['date']) if new['date'] else None
        if (old['date'] != new['date']
                or old['date_source'] != new['date_source']
                or old_name != new_name):
            mismatches.append((fp, old, new, old_name, new_name))

    print(f'Mismatches: {len(mismatches)} / {len(filepaths)}')
    for fp, old, new, old_name, new_name in mismatches[:30]:
        print(f'  {Path(fp).name}')
        print(f'    old: date={old["date"]} source={old["date_source"]} name={old_name}')
        print(f'    new: date={new["date"]} source={new["date_source"]} name={new_name}')

    # Also confirm read_metadata() itself (single-file path) is unchanged,
    # on a sample, since re-running it for all 2000 files sequentially is slow.
    sample = filepaths[:20] + filepaths[-20:]
    print()
    print('--- Sampling refactored single-file read_metadata() (40 files) ---')
    single_mismatches = 0
    for fp in sample:
        old = old_mr.read_metadata(fp)
        new = new_mr.read_metadata(fp)
        if old['date'] != new['date'] or old['date_source'] != new['date_source']:
            single_mismatches += 1
            print(f'  MISMATCH {Path(fp).name}: old={old["date"]}/{old["date_source"]} new={new["date"]}/{new["date_source"]}')
    print(f'Single-file mismatches: {single_mismatches} / {len(sample)}')


if __name__ == '__main__':
    main()
