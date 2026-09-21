"""
Verify step 3: compare old read_metadata() (frozen scratch snapshot) against
the refactored read_metadata() and the new read_metadata_batch() over the
same real folder. Any difference in date/date_source/proposed filename is a
bug unless explained.

Not a pytest test — run directly: python tests/diagnostic_batch_diff.py <folder>
"""
import importlib.util
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SNAPSHOT_PATH = (
    r'C:\Users\MATTJE~1\AppData\Local\Temp\claude\d--Documents-Work-Projects-MediaReel'
    r'\d8678866-1774-4e94-9457-d9de1554b49b\scratchpad\metadata_reader_baseline_snapshot.py'
)

spec = importlib.util.spec_from_file_location('metadata_reader_old', SNAPSHOT_PATH)
old_mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mr)

import metadata_reader as new_mr
from metadata_reader import build_new_filename, SUPPORTED_EXTENSIONS

# The snapshot module's _vendor_path() resolves relative to its own __file__,
# which is the scratchpad directory, not the project root — so it would
# never find the real vendor/exiftool.exe and would silently fall back to
# filename parsing for every file. Point it at the real resolver instead.
old_mr._vendor_path = new_mr._vendor_path


def collect_files(folder: str):
    p = Path(folder)
    return sorted([
        str(f) for f in p.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ])


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else r'D:\Pictures\2025'
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
