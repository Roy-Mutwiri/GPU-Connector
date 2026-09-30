"""Headless startup/layout smoke test and reproducible screenshot."""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from gpu_link.ui.window import Window  # noqa: E402

app = QApplication([])
# The offscreen Qt platform has no native Windows font enumeration.
for filename in ("segoeui.ttf", "segoeuib.ttf"):
    QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
# Prevent first-run prompt without changing persisted preferences.
Window.first_run = lambda self: None
window = Window()
window.show()

def finish():
    Path("artifacts").mkdir(exist_ok=True)
    window.grab().save("artifacts/dashboard.png")
    window.close()

QTimer.singleShot(12000, finish)
raise SystemExit(app.exec())
