import os
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
)

from core.lyric_exporter import (
    KscOptions,
    LYRIC_FORMAT_LABELS,
    LYRIC_PART_LABELS,
    META_LANG_LABELS,
    export_song_lyrics,
)
from core.parser import collect_json_files, load_song_json
from ui.widgets import DragLineEdit

LYRIC_FIELD_OPTIONS = [
    ("原文歌词", "ori"),
    ("韩文歌词", "ko"),
    ("罗马音", "rom"),
    ("英文翻译", "en"),
]


@dataclass
class LyricExportResult:
    success: list[str]
    failed: list[tuple[str, str]]


class LyricExportWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(
        self,
        json_paths: list[str],
        output_dir: str,
        lyric_field: str,
        lyric_format: str,
        part: str,
        title_lang: str,
        artist_lang: str,
        ksc_options: KscOptions,
        parent=None,
    ):
        super().__init__(parent)
        self.json_paths = json_paths
        self.output_dir = output_dir
        self.lyric_field = lyric_field
        self.lyric_format = lyric_format
        self.part = part
        self.title_lang = title_lang
        self.artist_lang = artist_lang
        self.ksc_options = ksc_options

    def run(self):
        result = LyricExportResult(success=[], failed=[])
        total = len(self.json_paths)

        for index, path in enumerate(self.json_paths, start=1):
            name = os.path.basename(path)
            self.progress.emit(int(index / total * 100), f"正在处理: {name}")
            try:
                song = load_song_json(path, self.lyric_field)
                output_path = export_song_lyrics(
                    song,
                    self.output_dir,
                    lyric_format=self.lyric_format,
                    part=self.part,
                    title_lang=self.title_lang,
                    artist_lang=self.artist_lang,
                    ksc_options=self.ksc_options,
                )
                result.success.append(output_path)
            except Exception as exc:
                result.failed.append((path, str(exc)))

        self.finished.emit(result)


class LyricExportPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("lyricExportPage")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("歌词导出"))
        layout.addWidget(
            BodyLabel(
                "从 JSON 提取歌名、歌手与分句歌词，支持 KSC小灰熊 (.txt) / KSC / TXT / LRC / CSV。"
            )
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

        row1 = QHBoxLayout()
        row1.addWidget(BodyLabel("歌词内容:"))
        self.lyric_combo = ComboBox(option_card)
        for label, _ in LYRIC_FIELD_OPTIONS:
            self.lyric_combo.addItem(label)
        row1.addWidget(self.lyric_combo)
        row1.addWidget(BodyLabel("声部:"))
        self.part_combo = ComboBox(option_card)
        for label, _ in LYRIC_PART_LABELS:
            self.part_combo.addItem(label)
        row1.addWidget(self.part_combo)
        row1.addStretch(1)
        option_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("歌词格式:"))
        self.format_combo = ComboBox(option_card)
        for label, _, _ in LYRIC_FORMAT_LABELS:
            self.format_combo.addItem(label)
        self.format_combo.currentIndexChanged.connect(self._update_ksc_option_visibility)
        row2.addWidget(self.format_combo)
        row2.addStretch(1)
        option_layout.addLayout(row2)

        ksc_row = QHBoxLayout()
        self.char_bracket_checkbox = CheckBox("字符中括号格式", option_card)
        self.word_bracket_checkbox = CheckBox("单词中括号格式", option_card)
        ksc_row.addWidget(self.char_bracket_checkbox)
        ksc_row.addWidget(self.word_bracket_checkbox)
        ksc_row.addStretch(1)
        option_layout.addLayout(ksc_row)
        self._ksc_option_widgets = [self.char_bracket_checkbox, self.word_bracket_checkbox]

        row3 = QHBoxLayout()
        row3.addWidget(BodyLabel("歌名:"))
        self.title_lang_combo = ComboBox(option_card)
        for label, _ in META_LANG_LABELS:
            self.title_lang_combo.addItem(label)
        row3.addWidget(self.title_lang_combo)
        row3.addWidget(BodyLabel("歌手:"))
        self.artist_lang_combo = ComboBox(option_card)
        for label, _ in META_LANG_LABELS:
            self.artist_lang_combo.addItem(label)
        row3.addWidget(self.artist_lang_combo)
        row3.addStretch(1)
        option_layout.addLayout(row3)
        layout.addWidget(option_card)

        output_card = CardWidget(container)
        output_layout = QVBoxLayout(output_card)
        output_layout.addWidget(StrongBodyLabel("输出目录"))
        output_row = QHBoxLayout()
        self.output_edit = DragLineEdit(output_card)
        self.output_edit.setPlaceholderText("拖拽或选择歌词输出文件夹")
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

        self.worker: LyricExportWorker | None = None
        self._update_ksc_option_visibility()

    def _current_lyric_format(self) -> str:
        return LYRIC_FORMAT_LABELS[self.format_combo.currentIndex()][1]

    def _update_ksc_option_visibility(self):
        is_ksc = self._current_lyric_format() in ("ksc-txt", "ksc")
        for widget in self._ksc_option_widgets:
            widget.setVisible(is_ksc)

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
        lyric_field = LYRIC_FIELD_OPTIONS[self.lyric_combo.currentIndex()][1]
        lyric_format = self._current_lyric_format()
        part = LYRIC_PART_LABELS[self.part_combo.currentIndex()][1]
        title_lang = META_LANG_LABELS[self.title_lang_combo.currentIndex()][1]
        artist_lang = META_LANG_LABELS[self.artist_lang_combo.currentIndex()][1]
        ksc_options = KscOptions(
            char_bracket=self.char_bracket_checkbox.isChecked(),
            word_bracket=self.word_bracket_checkbox.isChecked(),
        )

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
                "请选择歌词文件的输出目录。",
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

        self.worker = LyricExportWorker(
            json_paths=json_paths,
            output_dir=output_dir,
            lyric_field=lyric_field,
            lyric_format=lyric_format,
            part=part,
            title_lang=title_lang,
            artist_lang=artist_lang,
            ksc_options=ksc_options,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, _value: int, message: str):
        self.export_btn.setText(message)

    def _on_finished(self, result: LyricExportResult):
        self.export_btn.setEnabled(True)
        self.export_btn.setText("开始导出")

        if result.success and not result.failed:
            InfoBar.success(
                "导出完成",
                f"成功导出 {len(result.success)} 个歌词文件。",
                duration=4000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return

        lines = [f"成功: {len(result.success)} 个歌词文件"]
        if result.failed:
            lines.append(f"失败: {len(result.failed)} 个 JSON 文件")
            for path, reason in result.failed[:8]:
                lines.append(f"- {os.path.basename(path)}: {reason}")

        box = MessageBox("导出结果", "\n".join(lines), self.window())
        box.yesButton.setText("确定")
        box.cancelButton.hide()
        box.exec()
