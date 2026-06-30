"""启动画面：开发模式用 Qt Splash；打包模式由 PyInstaller bootloader 提前显示。"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QSplashScreen

_SPLASH_WIDTH = 360
_SPLASH_HEIGHT = 148
_APP_TITLE = "MS JSON 导出工具"
_BOOT_STATUS = "正在启动…"
LOADING_STATUS = "正在加载界面…"


def is_boot_splash_active() -> bool:
    try:
        import pyi_splash
    except ImportError:
        return False
    return pyi_splash.is_alive()


def close_boot_splash() -> None:
    try:
        import pyi_splash
    except ImportError:
        return
    if pyi_splash.is_alive():
        pyi_splash.close()


def _splash_pixmap(status: str = _BOOT_STATUS) -> QPixmap:
    pixmap = QPixmap(_SPLASH_WIDTH, _SPLASH_HEIGHT)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    background = QColor(32, 32, 32)
    border = QColor(72, 72, 72)
    path = QPainterPath()
    path.addRoundedRect(0.5, 0.5, _SPLASH_WIDTH - 1, _SPLASH_HEIGHT - 1, 12, 12)
    painter.fillPath(path, background)
    painter.setPen(border)
    painter.drawPath(path)

    title_font = QFont()
    title_font.setPointSize(13)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.setPen(QColor(245, 245, 245))
    painter.drawText(
        0,
        28,
        _SPLASH_WIDTH,
        32,
        Qt.AlignmentFlag.AlignHCenter,
        _APP_TITLE,
    )

    status_font = QFont()
    status_font.setPointSize(10)
    painter.setFont(status_font)
    painter.setPen(QColor(160, 160, 160))
    painter.drawText(
        0,
        58,
        _SPLASH_WIDTH,
        28,
        Qt.AlignmentFlag.AlignHCenter,
        status,
    )
    painter.end()
    return pixmap


def create_app_splash(status: str = _BOOT_STATUS) -> QSplashScreen:
    splash = QSplashScreen(_splash_pixmap(status), Qt.WindowType.WindowStaysOnTopHint)
    splash.setEnabled(False)
    return splash
