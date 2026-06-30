import ctypes
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import setTheme, Theme

from ui.splash import (
    LOADING_STATUS,
    close_boot_splash,
    create_app_splash,
    is_boot_splash_active,
)


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def main():
    try:
        myappid = "msjson.midi.exporter.v1"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    setTheme(Theme.AUTO)

    icon_path = os.path.join(_app_dir(), "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    if is_boot_splash_active():
        qt_splash = create_app_splash(LOADING_STATUS)
        qt_splash.show()
        app.processEvents()
        close_boot_splash()
    else:
        qt_splash = create_app_splash()
        qt_splash.show()
        app.processEvents()

    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    qt_splash.finish(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
