"""
Test whether sharding read_metadata_batch() across N concurrent persistent
exiftool processes recovers the parallelism lost by using a single process,
while still avoiding one-exiftool-launch-per-file. Not a pytest test.

Usage: python tests/diagnostic_shard_timing.py <folder> [shard_counts...]
"""
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent.parent))

from metadata_reader import read_metadata_batch, SUPPORTED_EXTENSIONS


def collect_files(folder: str):
    p = Path(folder)
    return sorted([
        str(f) for f in p.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ])


def run_sharded(filepaths, n_shards: int):
    n = len(filepaths)
    shard_size = (n + n_shards - 1) // n_shards
    shards = [filepaths[i:i + shard_size] for i in range(0, n, shard_size)]
    shards = [s for s in shards if s]

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(shards)) as ex:
        results = list(ex.map(read_metadata_batch, shards))
    elapsed = time.perf_counter() - t0
    total_files = sum(len(r) for r in results)
    assert total_files == n, f'{total_files} != {n}'
    return elapsed


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    folder = sys.argv[1]
    shard_counts = [int(x) for x in sys.argv[2:]] or [4, 8, 16]

    filepaths = collect_files(folder)
    print(f'Folder: {folder}, files: {len(filepaths)}')
    print()

    for n_shards in shard_counts:
        elapsed = run_sharded(filepaths, n_shards)
        print(f'{n_shards:>3} shards: {elapsed:.2f}s  ({elapsed / len(filepaths) * 1000:.1f}ms/file)')


if __name__ == '__main__':
    main()
