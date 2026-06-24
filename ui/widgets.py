from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, CompactSpinBox, LineEdit, ProgressBar
from qfluentwidgets.common.style_sheet import setCustomStyleSheet
from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu

COMPACT_CONTROL_HEIGHT = 28

_COMBO_BORDERLESS_QSS = """
ComboBox, ModelComboBox {
    border: none;
    border-top: none;
    outline: none;
}
ComboBox:pressed, ModelComboBox:pressed {
    border: none;
    border-top: none;
}
ComboBox:disabled, ModelComboBox:disabled {
    border: none;
    border-top: none;
}
"""

_COMBO_MENU_BORDERLESS_QSS = """
MenuActionListWidget {
    border: none;
    outline: none;
}
"""


class BorderlessComboBoxMenu(ComboBoxMenu):
    def __init__(self, parent=None):
        super().__init__(parent)
        setCustomStyleSheet(self, _COMBO_MENU_BORDERLESS_QSS, _COMBO_MENU_BORDERLESS_QSS)
        self.view.setGraphicsEffect(None)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)


class BorderlessComboBox(ComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        setCustomStyleSheet(self, _COMBO_BORDERLESS_QSS, _COMBO_BORDERLESS_QSS)

    def _createComboMenu(self):
        return BorderlessComboBoxMenu(self)


class DragLineEdit(LineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.setText(path)


def create_compact_combo(
    parent=None,
    *,
    min_width: int = 88,
    max_width: int = 180,
) -> BorderlessComboBox:
    combo = BorderlessComboBox(parent)
    combo.setFixedHeight(COMPACT_CONTROL_HEIGHT)
    combo.setMinimumWidth(min_width)
    combo.setMaximumWidth(max_width)
    return combo


def create_offset_spinbox(parent=None) -> CompactSpinBox:
    spinbox = CompactSpinBox(parent)
    spinbox.setRange(-999_999, 999_999)
    spinbox.setValue(0)
    spinbox.setSuffix(" ms")
    spinbox.setAccelerated(True)
    spinbox.setFixedHeight(COMPACT_CONTROL_HEIGHT)
    spinbox.setFixedWidth(108)
    return spinbox


class BatchProgressPanel(QWidget):
    """批量任务进度条与状态文字。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status_label = BodyLabel("", self)
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)

        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        self.setVisible(False)

    def start(self, message: str = "正在处理…") -> None:
        self.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText(message)

    def update(self, value: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, value)))
        self.status_label.setText(message)

    def finish(self) -> None:
        self.progress_bar.setValue(100)
        self.setVisible(False)
        self.status_label.setText("")
