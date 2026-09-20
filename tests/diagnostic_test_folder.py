import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from media_model import MediaTableModel
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop
import sys

app = QApplication(sys.argv)
model = MediaTableModel()

loop = QEventLoop()
model.folder_load_complete.connect(loop.quit)
model.load_folder(r'D:\Pictures\2026\2026-06-30 - Fiji Family Trip\Silvia Fiji photos')
loop.exec()

print('Attention count:', model.attention_count())
print()
print('Needs attention files:')
for f in model.files():
    if f.needs_attention:
        print(f' - {f.filename} | source={f.date_source} | moved={f.user_moved}')

print(f'Total files: {model.rowCount()}')
print()
for f in model.files():
    print(f'{f.filename}')
    print(f'  source={f.date_source} | date={f.date} | proposed={f.proposed_filename}')