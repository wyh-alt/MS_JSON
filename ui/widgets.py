from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import QAbstractSpinBox, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, CompactSpinBox, LineEdit, ProgressBar, TransparentToolButton
from qfluentwidgets.common.style_sheet import setCustomStyleSheet
from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu
from qfluentwidgets.components.widgets.spin_box import SpinIcon

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


def create_compact_spinbox(
    parent=None,
    *,
    minimum: int,
    maximum: int,
    value: int,
    suffix: str = "",
    width: int = 108,
) -> CompactSpinBox:
    spinbox = CompactSpinBox(parent)
    spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spinbox.setRange(minimum, maximum)
    spinbox.setValue(value)
    spinbox.setAccelerated(False)
    if suffix:
        spinbox.setSuffix(suffix)
    spinbox.setFixedHeight(COMPACT_CONTROL_HEIGHT)
    spinbox.setMinimumWidth(width)
    spinbox.setFixedWidth(width)
    return spinbox


COMPACT_CONTROL_HEIGHT = 28
SIGNED_INPUT_BOX_WIDTH = 68
SIGNED_INPUT_SUFFIX_WIDTH = 32
SIGNED_INPUT_STEPPER_WIDTH = 16


class _StepperColumn(QWidget):
    """竖排上下步进按钮，嵌入 LineEdit 右侧。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.up_button = TransparentToolButton(SpinIcon.UP, self)
        self.down_button = TransparentToolButton(SpinIcon.DOWN, self)
        button_height = max(12, (COMPACT_CONTROL_HEIGHT - 2) // 2)
        for button in (self.up_button, self.down_button):
            button.setFixedSize(SIGNED_INPUT_STEPPER_WIDTH, button_height)
            button.setIconSize(QSize(8, 8))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.ArrowCursor)
        layout.addWidget(self.up_button)
        layout.addWidget(self.down_button)
        self.setFixedSize(SIGNED_INPUT_STEPPER_WIDTH, COMPACT_CONTROL_HEIGHT)
        self.setCursor(Qt.CursorShape.ArrowCursor)


class _InlineStepperInput(QWidget):
    """单行输入框，右侧内嵌上下步进按钮。"""

    def __init__(self, parent=None, *, width: int = SIGNED_INPUT_BOX_WIDTH):
        super().__init__(parent)
        self.setFixedSize(width, COMPACT_CONTROL_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.edit = LineEdit(self)
        self.edit.setFixedSize(width, COMPACT_CONTROL_HEIGHT)
        self.edit.hBoxLayout.setContentsMargins(6, 2, 2, 2)
        self.edit.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )

        self.stepper = _StepperColumn(self.edit)
        clear_index = self.edit.hBoxLayout.indexOf(self.edit.clearButton)
        self.edit.hBoxLayout.insertWidget(
            clear_index,
            self.stepper,
            0,
            Qt.AlignmentFlag.AlignRight,
        )
        self.up_button = self.stepper.up_button
        self.down_button = self.stepper.down_button

        margins = self.edit.textMargins()
        self.edit.setTextMargins(
            4,
            margins.top(),
            SIGNED_INPUT_STEPPER_WIDTH + 4,
            margins.bottom(),
        )

        layout.addWidget(self.edit)

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self.edit.setEnabled(enabled)
        self.up_button.setEnabled(enabled)
        self.down_button.setEnabled(enabled)


class CompactSignedInput(QWidget):
    """带后缀与内嵌步进按钮的紧凑数值输入。"""

    def __init__(
        self,
        parent=None,
        *,
        minimum: int,
        maximum: int,
        value: int,
        suffix: str = "",
        input_width: int = SIGNED_INPUT_BOX_WIDTH,
        suffix_width: int = SIGNED_INPUT_SUFFIX_WIDTH,
    ):
        super().__init__(parent)
        self._minimum = minimum
        self._maximum = maximum

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.input_field = _InlineStepperInput(self, width=input_width)
        layout.addWidget(self.input_field)

        suffix_text = suffix.strip()
        self.suffix_label = BodyLabel(suffix_text, self)
        self.suffix_label.setFixedHeight(COMPACT_CONTROL_HEIGHT)
        self.suffix_label.setFixedWidth(suffix_width)
        self.suffix_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self.suffix_label)

        self.setFixedHeight(COMPACT_CONTROL_HEIGHT)
        self.setFixedWidth(input_width + 6 + suffix_width)

        self.input_field.up_button.clicked.connect(self._increase)
        self.input_field.down_button.clicked.connect(self._decrease)
        self.input_field.edit.editingFinished.connect(self._normalize_text)
        self._set_value(value)

    @property
    def edit(self):
        return self.input_field.edit

    def _increase(self) -> None:
        self._set_value(self.value() + 1)

    def _decrease(self) -> None:
        self._set_value(self.value() - 1)

    def _set_value(self, value: int) -> None:
        clamped = max(self._minimum, min(self._maximum, int(value)))
        self.input_field.edit.setText(str(clamped))

    def _normalize_text(self) -> None:
        text = self.input_field.edit.text().strip()
        try:
            value = int(text)
        except ValueError:
            value = self._minimum
        self._set_value(value)

    def value(self) -> int:
        text = self.input_field.edit.text().strip()
        try:
            return max(self._minimum, min(self._maximum, int(text)))
        except ValueError:
            return self._minimum

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self.input_field.setEnabled(enabled)
        self.suffix_label.setEnabled(enabled)


def create_signed_value_input(
    parent=None,
    *,
    minimum: int,
    maximum: int,
    value: int,
    suffix: str = "",
    input_width: int = SIGNED_INPUT_BOX_WIDTH,
    suffix_width: int = SIGNED_INPUT_SUFFIX_WIDTH,
) -> CompactSignedInput:
    return CompactSignedInput(
        parent,
        minimum=minimum,
        maximum=maximum,
        value=value,
        suffix=suffix,
        input_width=input_width,
        suffix_width=suffix_width,
    )


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
