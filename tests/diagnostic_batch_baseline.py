"""
Baseline timing for the exiftool-batching task. Not a pytest test —
run directly: python tests/diagnostic_batch_baseline.py <folder>

Measures, on the CURRENT (pre-batch) code:
  1. Raw read_metadata() cost, sequential, no Qt — pure per-file exiftool cost.
  2. Thumbnail-only generation cost, sequential — isolates Pillow/ffmpeg cost.
  3. The real end-to-end app load_folder() path (QThreadPool concurrency,
     metadata + thumbnails interleaved, exactly as today).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from metadata_reader import read_metadata, SUPPORTED_EXTENSIONS


def collect_files(folder: str):
    p = Path(folder)
    return sorted([
        str(f) for f in p.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ])


def time_metadata_only(filepaths):
    t0 = time.perf_counter()
    results = [read_metadata(fp) for fp in filepaths]
    elapsed = time.perf_counter() - t0
    return elapsed, results


def time_thumbnails_only(filepaths):
    from media_model import MetadataWorker
    worker = MetadataWorker(0, filepaths[0])
    t0 = time.perf_counter()
    ok, fail = 0, 0
    for fp in filepaths:
        ext = Path(fp).suffix.lower()
        is_video = ext in {'.mp4', '.mov', '.avi'}
        img = worker._make_thumbnail(fp, is_video)
        if img is not None:
            ok += 1
        else:
            fail += 1
    elapsed = time.perf_counter() - t0
    return elapsed, ok, fail


def time_app_load(folder: str):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEventLoop
    from media_model import MediaTableModel

    app = QApplication.instance() or QApplication(sys.argv)
    model = MediaTableModel()
    loop = QEventLoop()
    model.folder_load_complete.connect(loop.quit)

    t0 = time.perf_counter()
    model.load_folder(folder)
    loop.exec()
    elapsed = time.perf_counter() - t0
    return elapsed, model.rowCount()


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else r'D:\Pictures\2025'
    filepaths = collect_files(folder)
    print(f'Folder: {folder}')
    print(f'Files:  {len(filepaths)}')
    print()

    print('--- 1. Metadata only (sequential, no Qt) ---')
    elapsed, results = time_metadata_only(filepaths)
    print(f'Total:   {elapsed:.2f}s')
    print(f'Per file: {elapsed / len(filepaths) * 1000:.1f}ms')
    from collections import Counter
    sources = Counter(r['date_source'] for r in results)
    for src, count in sources.most_common():
        print(f'  {src}: {count}')
    print()

    print('--- 2. Thumbnails only (sequential) ---')
    elapsed, ok, fail = time_thumbnails_only(filepaths)
    print(f'Total:   {elapsed:.2f}s')
    print(f'Per file: {elapsed / len(filepaths) * 1000:.1f}ms')
    print(f'OK: {ok}, Failed: {fail}')
    print()

    print('--- 3. Full app load_folder() (current concurrency, metadata+thumbs) ---')
    elapsed, count = time_app_load(folder)
    print(f'Total:   {elapsed:.2f}s')
    print(f'Files:   {count}')
    print(f'Per file: {elapsed / count * 1000:.1f}ms')


if __name__ == '__main__':
    main()
