import sys

from gpu_link.logging import configure


def main():
    configure()
    if "--self-test" in sys.argv:
        from gpu_link.app.selftest import run
        return run()
    smoke = "--gui-smoke" in sys.argv
    if smoke:
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from gpu_link.ui.window import Window
    app = QApplication(sys.argv)
    app.setApplicationName("GPU Link")
    if smoke:
        from pathlib import Path
        from PySide6.QtGui import QFontDatabase
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
        Window.first_run = lambda self: None
    window = Window()
    window.show()
    if smoke:
        from PySide6.QtCore import QTimer
        def finish():
            output = Path("artifacts/packaged-dashboard.png")
            output.parent.mkdir(exist_ok=True)
            window.grab().save(str(output))
            window.close()
        QTimer.singleShot(12000, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
