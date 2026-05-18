from media_model import MediaTableModel
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QEventLoop
import sys

app = QApplication(sys.argv)
model = MediaTableModel()

folder = r"D:\Pictures\2026\MJ 50"
print(f'Loading folder: {folder}')

# Use an event loop that exits cleanly when loading is complete
loop = QEventLoop()
model.folder_load_complete.connect(loop.quit)
model.load_folder(folder)
loop.exec()  # waits until all workers are done

print(f'Rows: {model.rowCount()}')
for i, f in enumerate(model.files()):
    print(f'  {i+1:3d}. {f.filename}')
    print(f'       date={f.date}  source={f.date_source}')
    print(f'       proposed={f.proposed_filename}')
    print(f'       interp={f.is_interpolated}  re_anchor={f.is_re_anchored}  attention={f.needs_attention}')