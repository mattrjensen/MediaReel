from media_model import MediaTableModel
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop
import sys

app = QApplication(sys.argv)
model = MediaTableModel()

loop = QEventLoop()
model.folder_load_complete.connect(loop.quit)
model.load_folder(r'D:\Pictures\2026\MJ 50')
loop.exec()

print('Attention count:', model.attention_count())
print()
print('Needs attention files:')
for f in model.files():
    if f.needs_attention:
        print(f' - {f.filename} | source={f.date_source} | moved={f.user_moved}')