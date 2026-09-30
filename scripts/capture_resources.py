"""Render the resource page with measured local telemetry for layout inspection."""
import os
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from gpu_link.ui.window import Window
from gpu_link.worker.resources import Resources

app = QApplication([])
for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisym.ttf'):
    QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / name))
Window.first_run = lambda self: None
window = Window()
window.nav.setCurrentRow(7)
window.status.setText('UI validation - measured local resources, not a remote connection')
resources = Resources()
window.show()

def sample():
    window.show_resources(resources.telemetry())

def finish():
    window.grab().save('artifacts/resources-page.png')
    window.close()

timer = QTimer()
timer.timeout.connect(sample)
timer.start(600)
QTimer.singleShot(4000, finish)
raise SystemExit(app.exec())
