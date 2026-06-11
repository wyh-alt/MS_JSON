import os
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
)

from core.midi_exporter import LYRIC_GRANULARITY_LABELS, PART_MODE_LABELS, export_song
from core.parser import collect_json_files, load_song_json
from ui.widgets import DragLineEdit

LYRIC_FIELD_OPTIONS = [
    ("原文歌词", "ori"),
    ("韩文歌词", "ko"),
    ("罗马音", "rom"),
    ("英文翻译", "en"),
]


@dataclass
class ExportResult:
    success: list[str]
    failed: list[tuple[str, str]]
    skipped: int


class ExportWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(
        self,
        json_paths: list[str],
        output_dir: str,
        part_mode: str,
        lyric_field: str,
        write_tempo: bool,
        write_lyrics: bool,
        lyric_granularity: str,
        lower_octave: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.json_paths = json_paths
        self.output_dir = output_dir
        self.part_mode = part_mode
        self.lyric_field = lyric_field
        self.write_tempo = write_tempo
        self.write_lyrics = write_lyrics
        self.lyric_granularity = lyric_granularity
        self.lower_octave = lower_octave

    def run(self):
        result = ExportResult(success=[], failed=[], skipped=0)
        total = len(self.json_paths)

        for index, path in enumerate(self.json_paths, start=1):
            name = os.path.basename(path)
            self.progress.emit(int(index / total * 100), f"正在处理: {name}")
            try:
                song = load_song_json(path, self.lyric_field)
                exported = export_song(
                    song,
                    self.output_dir,
                    self.part_mode,
                    write_tempo=self.write_tempo,
                    write_lyrics=self.write_lyrics,
                    lyric_granularity=self.lyric_granularity,
                    lower_octave=self.lower_octave,
                )
                result.success.extend(exported)
            except Exception as exc:
                result.failed.append((path, str(exc)))

        self.finished.emit(result)


class ExportPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("exportPage")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("MIDI 导出"))
        layout.addWidget(
            BodyLabel("拖入 JSON 文件或文件夹后，将自动处理其中全部有效 JSON。")
        )

        input_card = CardWidget(container)
        input_layout = QVBoxLayout(input_card)
        input_layout.addWidget(StrongBodyLabel("输入路径"))
        input_layout.addWidget(BodyLabel("支持单个 JSON 文件或文件夹（自动递归扫描）。"))

        input_row = QHBoxLayout()
        self.input_edit = DragLineEdit(input_card)
        self.input_edit.setPlaceholderText("拖拽或输入 JSON 文件/文件夹路径")
        browse_input_btn = PushButton("浏览", input_card)
        browse_input_btn.clicked.connect(self._browse_input)
        input_row.addWidget(self.input_edit)
        input_row.addWidget(browse_input_btn)
        input_layout.addLayout(input_row)
        layout.addWidget(input_card)

        option_card = CardWidget(container)
        option_layout = QVBoxLayout(option_card)
        option_layout.addWidget(StrongBodyLabel("导出选项"))

        option_row = QHBoxLayout()
        option_row.addWidget(BodyLabel("声部导出:"))
        self.part_combo = ComboBox(option_card)
        for label, _ in PART_MODE_LABELS:
            self.part_combo.addItem(label)
        self.tempo_checkbox = CheckBox("写入速度信息", option_card)
        self.tempo_checkbox.setChecked(False)
        self.lyrics_checkbox = CheckBox("写入歌词", option_card)
        self.lyrics_checkbox.setChecked(True)
        self.lower_octave_checkbox = CheckBox("音符降低八度", option_card)
        self.lower_octave_checkbox.setChecked(True)
        option_row.addWidget(self.part_combo)
        option_row.addWidget(self.tempo_checkbox)
        option_row.addWidget(self.lyrics_checkbox)
        option_row.addWidget(self.lower_octave_checkbox)
        option_row.addStretch(1)
        option_layout.addLayout(option_row)

        lyric_row = QHBoxLayout()
        lyric_row.addWidget(BodyLabel("歌词内容:"))
        self.lyric_combo = ComboBox(option_card)
        for label, _ in LYRIC_FIELD_OPTIONS:
            self.lyric_combo.addItem(label)
        lyric_row.addWidget(self.lyric_combo)
        lyric_row.addWidget(BodyLabel("歌词粒度:"))
        self.lyric_granularity_combo = ComboBox(option_card)
        for label, _ in LYRIC_GRANULARITY_LABELS:
            self.lyric_granularity_combo.addItem(label)
        lyric_row.addWidget(self.lyric_granularity_combo)
        lyric_row.addStretch(1)
        option_layout.addLayout(lyric_row)
        layout.addWidget(option_card)

        output_card = CardWidget(container)
        output_layout = QVBoxLayout(output_card)
        output_layout.addWidget(StrongBodyLabel("输出目录"))
        output_row = QHBoxLayout()
        self.output_edit = DragLineEdit(output_card)
        self.output_edit.setPlaceholderText("拖拽或选择 MIDI 输出文件夹")
        browse_output_btn = PushButton("浏览", output_card)
        browse_output_btn.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_edit)
        output_row.addWidget(browse_output_btn)
        output_layout.addLayout(output_row)
        layout.addWidget(output_card)

        action_row = QHBoxLayout()
        self.export_btn = PrimaryPushButton("开始导出", container)
        self.export_btn.clicked.connect(self._start_export)
        action_row.addStretch(1)
        action_row.addWidget(self.export_btn)
        layout.addLayout(action_row)
        layout.addStretch(1)

        self.worker: ExportWorker | None = None

    def _browse_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 JSON 文件",
            "",
            "JSON Files (*.json);;All Files (*)",
        )
        if path:
            self.input_edit.setText(path)
            return

        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self.input_edit.setText(folder)

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.output_edit.setText(folder)

    def _start_export(self):
        input_path = self.input_edit.text().strip()
        output_dir = self.output_edit.text().strip()
        part_mode = PART_MODE_LABELS[self.part_combo.currentIndex()][1]
        lyric_field = LYRIC_FIELD_OPTIONS[self.lyric_combo.currentIndex()][1]
        write_tempo = self.tempo_checkbox.isChecked()
        write_lyrics = self.lyrics_checkbox.isChecked()
        lyric_granularity = LYRIC_GRANULARITY_LABELS[
            self.lyric_granularity_combo.currentIndex()
        ][1]
        lower_octave = self.lower_octave_checkbox.isChecked()

        if not input_path or not os.path.exists(input_path):
            InfoBar.warning(
                "路径无效",
                "请输入或拖入有效的 JSON 文件/文件夹路径。",
                duration=3000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return

        json_paths = collect_json_files(input_path, valid_only=True)
        if not json_paths:
            InfoBar.warning(
                "未找到有效 JSON",
                "路径下没有包含 mnote 数据的有效 JSON 文件。",
                duration=3000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return

        if not output_dir:
            InfoBar.warning(
                "缺少输出目录",
                "请选择 MIDI 文件的输出目录。",
                duration=3000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return

        self.export_btn.setEnabled(False)
        InfoBar.info(
            "开始导出",
            f"共 {len(json_paths)} 个 JSON，请稍候…",
            duration=2000,
            parent=self.window(),
            position=InfoBarPosition.TOP,
        )

        self.worker = ExportWorker(
            json_paths=json_paths,
            output_dir=output_dir,
            part_mode=part_mode,
            lyric_field=lyric_field,
            write_tempo=write_tempo,
            write_lyrics=write_lyrics,
            lyric_granularity=lyric_granularity,
            lower_octave=lower_octave,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, _value: int, message: str):
        self.export_btn.setText(message)

    def _on_finished(self, result: ExportResult):
        self.export_btn.setEnabled(True)
        self.export_btn.setText("开始导出")

        if result.success and not result.failed:
            InfoBar.success(
                "导出完成",
                f"成功导出 {len(result.success)} 个 MIDI 文件。",
                duration=4000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return

        lines = [f"成功: {len(result.success)} 个 MIDI 文件"]
        if result.failed:
            lines.append(f"失败: {len(result.failed)} 个 JSON 文件")
            for path, reason in result.failed[:8]:
                lines.append(f"- {os.path.basename(path)}: {reason}")

        box = MessageBox("导出结果", "\n".join(lines), self.window())
        box.yesButton.setText("确定")
        box.cancelButton.hide()
        box.exec()

        if result.success:
            InfoBar.success(
                "部分完成",
                f"已导出 {len(result.success)} 个 MIDI 文件。",
                duration=3000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MS JSON 导出工具")
        self.resize(900, 640)

        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(icon_path))

        from ui.lyric_export_page import LyricExportPage

        self.lyric_export_page = LyricExportPage(self)
        self.lyric_export_page.setObjectName("lyricExportInterface")
        self.addSubInterface(self.lyric_export_page, FIF.DOCUMENT, "歌词导出")

        self.export_page = ExportPage(self)
        self.export_page.setObjectName("exportInterface")
        self.addSubInterface(self.export_page, FIF.MUSIC, "MIDI导出")
