"""
Interactive diagnostic script — run against a real folder to verify model
behaviour end-to-end with actual EXIF/video metadata.

Usage:
    python tests/test_model.py "D:\\path\\to\\event\\folder"

For best coverage, use a folder that contains a mix of:
  - JPEGs and HEICs with EXIF metadata (DSLR, iPhone photos)
  - Videos (.mp4, .mov) — especially iOS videos (QuickTime timestamps)
  - Files with date strings embedded in the filename (Signal, WhatsApp, screenshots)
  - Files with no useful date (received files, downloads, screenshots without dates)
  - Files already renamed with the YYYYMMDD_HHMMSS_ prefix

The script loads the folder, prints a per-file summary, and shows a
statistics block so you can quickly spot unexpected classifications.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop

from media_model import MediaTableModel

RESET  = '\033[0m'
BOLD   = '\033[1m'
RED    = '\033[31m'
YELLOW = '\033[33m'
GREEN  = '\033[32m'
CYAN   = '\033[36m'
GREY   = '\033[90m'


def badge(source):
    colours = {
        'metadata':      GREEN,
        'filename':      CYAN,
        'date modified': YELLOW,
        'interpolated':  YELLOW,
        'none':          RED,
    }
    c = colours.get(source, GREY)
    return f'{c}[{source}]{RESET}'


def flag(f):
    parts = []
    if f.is_interpolated:  parts.append(f'{YELLOW}interp{RESET}')
    if f.is_re_anchored:   parts.append(f'{YELLOW}re-anchor{RESET}')
    if f.needs_attention:  parts.append(f'{RED}attention{RESET}')
    return ' '.join(parts) if parts else ''


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    folder = sys.argv[1]

    app = QApplication.instance() or QApplication(sys.argv)
    model = MediaTableModel()

    loop = QEventLoop()
    model.folder_load_complete.connect(loop.quit)
    model.load_folder(folder)
    loop.exec()

    files = model.files()
    n = len(files)
    print(f'\n{BOLD}Loaded {n} files from: {folder}{RESET}\n')
    print(f'  {"#":>4}  {"Source":<14}  {"Date":<20}  {"Proposed filename":<50}  Flags')
    print(f'  {"-"*4}  {"-"*14}  {"-"*20}  {"-"*50}  -----')

    for i, f in enumerate(files, 1):
        date_str = f.date.strftime('%Y-%m-%d %H:%M:%S') if f.date else '—'
        src = 'interpolated' if (f.is_interpolated or f.is_re_anchored) else f.date_source
        flags = flag(f)
        proposed = f.proposed_filename[:50]
        if f.proposed_filename.startswith('---'):
            proposed = f'{GREY}{proposed}{RESET}'
        elif f.proposed_filename != f.filename:
            proposed = f'{YELLOW}{proposed}{RESET}'
        print(f'  {i:>4}  {badge(src):<23}  {date_str:<20}  {proposed:<50}  {flags}')

    print()
    counts = {
        'metadata':     sum(1 for f in files if f.date_source == 'metadata' and not f.is_interpolated and not f.is_re_anchored),
        'filename':     sum(1 for f in files if f.date_source == 'filename' and not f.is_interpolated),
        'date modified':sum(1 for f in files if f.date_source == 'date modified'),
        'none':         sum(1 for f in files if f.date_source == 'none'),
        'interpolated': sum(1 for f in files if f.is_interpolated),
        're-anchored':  sum(1 for f in files if f.is_re_anchored),
        'hard anchor':  sum(1 for f in files if f.is_already_formatted),
    }
    will_rename  = sum(1 for f in files if f.proposed_filename != f.filename and not f.proposed_filename.startswith('---'))
    need_attn    = model.attention_count()
    phase        = 'Phase 1 (strong renames pending)' if model.has_pending_strong_renames() else 'Phase 2 (ready to order weak files)'

    print(f'{BOLD}Summary{RESET}')
    for label, count in counts.items():
        if count:
            print(f'  {label:<16} {count}')
    print(f'  {"will rename":<16} {will_rename}')
    print(f'  {"need attention":<16} {need_attn}')
    print(f'\n  {BOLD}Phase:{RESET} {phase}')
    print()


if __name__ == '__main__':
    main()
