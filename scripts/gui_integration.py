"""Exercise live UI, TLS worker, full test, and all page layouts over loopback."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from gpu_link.security import Identity  # noqa: E402
from gpu_link.ui.window import Window  # noqa: E402
from gpu_link.worker import Worker  # noqa: E402

app = QApplication([])
for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
    QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
temporary = tempfile.TemporaryDirectory(prefix="gpu-link-ui-")
worker = Worker("127.0.0.1", 0, Identity(temporary.name), discovery=False, allow_loopback=True)
worker.start()
Window.first_run = lambda self: None
window = Window()
window.worker_box.hide()
window.mode_label.setText("MAIN / CONTROLLER • LOOPBACK TEST")
window.show()
window.credentials = {"host": "127.0.0.1", "port": worker.port, "token": worker.identity.token,
                      "fingerprint": worker.identity.fingerprint, "allow_loopback": True}
window.engine.submit("connect", **window.credentials)
state = {"started": False, "passed": False}

def tick():
    if window.connected and not state["started"]:
        state["started"] = True
        window.run_test("full")
    if window.verified and window.task and not window.task.isRunning():
        state["passed"] = True
        timer.stop()
        Path("artifacts").mkdir(exist_ok=True)
        for index, name in enumerate(("dashboard-live", "connection", "benchmark", "stress", "diagnostics", "logs", "settings")):
            window.nav.setCurrentRow(index)
            app.processEvents()
            window.grab().save(f"artifacts/{name}.png")
        window.close()

timer = QTimer()
timer.timeout.connect(tick)
timer.start(300)
QTimer.singleShot(60000, window.close)
app.exec()
worker.stop()
temporary.cleanup()
print("GUI integration PASS" if state["passed"] else "GUI integration FAIL")
raise SystemExit(0 if state["passed"] else 1)
