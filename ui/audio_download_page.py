import os
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
)

from core.audio_downloader import (
    AUDIO_CONTENT_LABELS,
    KEY_MODE_LABELS,
    M4A_BITRATE_LABELS,
    M4A_CODEC_LABELS,
    MP3_BITRATE_LABELS,
    NAMING_FORMAT_LABELS,
    OUTPUT_FORMAT_LABELS,
    PCM_BIT_DEPTH_LABELS,
    SAMPLE_RATE_LABELS,
    AudioDownloadOptions,
    export_song_audio,
)
from core.lyric_exporter import META_LANG_LABELS
from core.parser import collect_json_files, load_song_json
from ui.widgets import BatchProgressPanel, DragLineEdit, create_compact_combo


@dataclass
class AudioDownloadResult:
    success: list[str]
    failed: list[tuple[str, str]]


class AudioDownloadWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(
        self,
        json_paths: list[str],
        output_dir: str,
        options: AudioDownloadOptions,
        parent=None,
    ):
        super().__init__(parent)
        self.json_paths = json_paths
        self.output_dir = output_dir
        self.options = options

    def run(self):
        result = AudioDownloadResult(success=[], failed=[])
        total = len(self.json_paths)

        for index, path in enumerate(self.json_paths, start=1):
            name = os.path.basename(path)
            self.progress.emit(int(index / total * 100), f"正在处理: {name}")
            try:
                song = load_song_json(path)
                output_path = export_song_audio(song, self.output_dir, self.options)
                result.success.append(output_path)
            except Exception as exc:
                result.failed.append((path, str(exc)))

        self.finished.emit(result)


class AudioDownloadPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("audioDownloadPage")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("音频资源下载"))
        layout.addWidget(
            BodyLabel(
                "从 JSON 批量下载 MR 音频资源，支持单轨导出与合并伴奏混音，"
                "并按指定格式转码输出。"
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

        source_card = CardWidget(container)
        source_layout = QVBoxLayout(source_card)
        source_layout.setSpacing(8)
        source_layout.addWidget(StrongBodyLabel("音频选项"))

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_row.addWidget(BodyLabel("音频内容:"))
        self.content_combo = create_compact_combo(source_card, min_width=260, max_width=340)
        for label, _ in AUDIO_CONTENT_LABELS:
            self.content_combo.addItem(label)
        source_row.addWidget(self.content_combo)
        source_row.addWidget(BodyLabel("调性:"))
        self.key_combo = create_compact_combo(source_card, min_width=96, max_width=120)
        for label, _ in KEY_MODE_LABELS:
            self.key_combo.addItem(label)
        source_row.addWidget(self.key_combo)
        source_row.addStretch(1)
        source_layout.addLayout(source_row)
        layout.addWidget(source_card)

        output_option_card = CardWidget(container)
        output_option_layout = QVBoxLayout(output_option_card)
        output_option_layout.setSpacing(8)
        output_option_layout.addWidget(StrongBodyLabel("输出设置"))

        format_row = QHBoxLayout()
        format_row.setSpacing(8)
        format_row.addWidget(BodyLabel("输出格式:"))
        self.format_combo = create_compact_combo(output_option_card, min_width=72, max_width=96)
        for label, _ in OUTPUT_FORMAT_LABELS:
            self.format_combo.addItem(label)
        format_row.addWidget(self.format_combo)

        self.sample_rate_label = BodyLabel("采样率:", output_option_card)
        format_row.addWidget(self.sample_rate_label)
        self.sample_rate_combo = create_compact_combo(output_option_card, min_width=96, max_width=120)
        for label, _ in SAMPLE_RATE_LABELS:
            self.sample_rate_combo.addItem(label)
        format_row.addWidget(self.sample_rate_combo)

        self.pcm_label = BodyLabel("PCM:", output_option_card)
        format_row.addWidget(self.pcm_label)
        self.pcm_combo = create_compact_combo(output_option_card, min_width=72, max_width=96)
        for label, _ in PCM_BIT_DEPTH_LABELS:
            self.pcm_combo.addItem(label)
        format_row.addWidget(self.pcm_combo)

        self.bitrate_label = BodyLabel("比特率:", output_option_card)
        format_row.addWidget(self.bitrate_label)
        self.mp3_bitrate_combo = create_compact_combo(output_option_card, min_width=88, max_width=108)
        for label, _ in MP3_BITRATE_LABELS:
            self.mp3_bitrate_combo.addItem(label)
        self.mp3_bitrate_combo.setCurrentIndex(len(MP3_BITRATE_LABELS) - 1)
        format_row.addWidget(self.mp3_bitrate_combo)
        self.m4a_bitrate_combo = create_compact_combo(output_option_card, min_width=88, max_width=108)
        for label, _ in M4A_BITRATE_LABELS:
            self.m4a_bitrate_combo.addItem(label)
        self.m4a_bitrate_combo.setCurrentIndex(2)
        format_row.addWidget(self.m4a_bitrate_combo)

        self.m4a_codec_label = BodyLabel("编码:", output_option_card)
        format_row.addWidget(self.m4a_codec_label)
        self.m4a_codec_combo = create_compact_combo(output_option_card, min_width=72, max_width=96)
        for label, _ in M4A_CODEC_LABELS:
            self.m4a_codec_combo.addItem(label)
        format_row.addWidget(self.m4a_codec_combo)

        format_row.addStretch(1)
        output_option_layout.addLayout(format_row)

        naming_row = QHBoxLayout()
        naming_row.setSpacing(8)
        naming_row.addWidget(BodyLabel("命名格式:"))
        self.naming_combo = create_compact_combo(output_option_card, min_width=140, max_width=180)
        for label, _ in NAMING_FORMAT_LABELS:
            self.naming_combo.addItem(label)
        naming_row.addWidget(self.naming_combo)
        naming_row.addWidget(BodyLabel("歌名:"))
        self.title_lang_combo = create_compact_combo(output_option_card, min_width=72, max_width=96)
        for label, _ in META_LANG_LABELS:
            self.title_lang_combo.addItem(label)
        naming_row.addWidget(self.title_lang_combo)
        naming_row.addWidget(BodyLabel("歌手:"))
        self.artist_lang_combo = create_compact_combo(output_option_card, min_width=72, max_width=96)
        for label, _ in META_LANG_LABELS:
            self.artist_lang_combo.addItem(label)
        naming_row.addWidget(self.artist_lang_combo)
        naming_row.addStretch(1)
        output_option_layout.addLayout(naming_row)
        layout.addWidget(output_option_card)

        output_card = CardWidget(container)
        output_layout = QVBoxLayout(output_card)
        output_layout.addWidget(StrongBodyLabel("输出目录"))
        output_row = QHBoxLayout()
        self.output_edit = DragLineEdit(output_card)
        self.output_edit.setPlaceholderText("拖拽或选择音频输出文件夹")
        browse_output_btn = PushButton("浏览", output_card)
        browse_output_btn.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_edit)
        output_row.addWidget(browse_output_btn)
        output_layout.addLayout(output_row)
        layout.addWidget(output_card)

        self.progress_panel = BatchProgressPanel(container)
        layout.addWidget(self.progress_panel)

        action_row = QHBoxLayout()
        self.export_btn = PrimaryPushButton("开始下载", container)
        self.export_btn.clicked.connect(self._start_export)
        action_row.addStretch(1)
        action_row.addWidget(self.export_btn)
        layout.addLayout(action_row)
        layout.addStretch(1)

        self.worker: AudioDownloadWorker | None = None
        self.format_combo.currentIndexChanged.connect(self._update_format_options)
        self.m4a_codec_combo.currentIndexChanged.connect(self._update_format_options)
        self._update_format_options()

    def _current_output_format(self) -> str:
        return OUTPUT_FORMAT_LABELS[self.format_combo.currentIndex()][1]

    def _current_m4a_codec(self) -> str:
        return M4A_CODEC_LABELS[self.m4a_codec_combo.currentIndex()][1]

    def _update_format_options(self):
        fmt = self._current_output_format()
        show_pcm = fmt in ("wav", "flac")
        show_mp3_bitrate = fmt == "mp3"
        show_m4a_options = fmt == "m4a"
        show_m4a_bitrate = show_m4a_options and self._current_m4a_codec() == "aac"

        self.sample_rate_label.setVisible(True)
        self.sample_rate_combo.setVisible(True)

        self.pcm_label.setVisible(show_pcm)
        self.pcm_combo.setVisible(show_pcm)

        self.bitrate_label.setVisible(show_mp3_bitrate or show_m4a_bitrate)
        self.mp3_bitrate_combo.setVisible(show_mp3_bitrate)
        self.m4a_bitrate_combo.setVisible(show_m4a_bitrate)

        self.m4a_codec_label.setVisible(show_m4a_options)
        self.m4a_codec_combo.setVisible(show_m4a_options)

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

    def _build_options(self) -> AudioDownloadOptions:
        fmt = self._current_output_format()
        bitrate_kbps = 320
        if fmt == "mp3":
            bitrate_kbps = MP3_BITRATE_LABELS[self.mp3_bitrate_combo.currentIndex()][1]
        elif fmt == "m4a" and self._current_m4a_codec() == "aac":
            bitrate_kbps = M4A_BITRATE_LABELS[self.m4a_bitrate_combo.currentIndex()][1]

        return AudioDownloadOptions(
            content=AUDIO_CONTENT_LABELS[self.content_combo.currentIndex()][1],
            key_mode=KEY_MODE_LABELS[self.key_combo.currentIndex()][1],
            output_format=fmt,
            sample_rate=SAMPLE_RATE_LABELS[self.sample_rate_combo.currentIndex()][1],
            pcm_bit_depth=PCM_BIT_DEPTH_LABELS[self.pcm_combo.currentIndex()][1],
            bitrate_kbps=bitrate_kbps,
            m4a_codec=M4A_CODEC_LABELS[self.m4a_codec_combo.currentIndex()][1],
            naming_format=NAMING_FORMAT_LABELS[self.naming_combo.currentIndex()][1],
            title_lang=META_LANG_LABELS[self.title_lang_combo.currentIndex()][1],
            artist_lang=META_LANG_LABELS[self.artist_lang_combo.currentIndex()][1],
        )

    def _validate_paths(self) -> list[str] | None:
        input_path = self.input_edit.text().strip()
        output_dir = self.output_edit.text().strip()

        if not input_path or not os.path.exists(input_path):
            InfoBar.warning(
                "路径无效",
                "请输入或拖入有效的 JSON 文件/文件夹路径。",
                duration=3000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return None

        json_paths = collect_json_files(input_path, valid_only=True)
        if not json_paths:
            InfoBar.warning(
                "未找到有效 JSON",
                "路径下没有包含 mnote 数据的有效 JSON 文件。",
                duration=3000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return None

        if not output_dir:
            InfoBar.warning(
                "缺少输出目录",
                "请选择音频文件的输出目录。",
                duration=3000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return None

        return json_paths

    def _start_export(self):
        json_paths = self._validate_paths()
        if json_paths is None:
            return

        output_dir = self.output_edit.text().strip()
        options = self._build_options()

        self.export_btn.setEnabled(False)
        self.progress_panel.start(f"共 {len(json_paths)} 个 JSON，准备下载…")
        InfoBar.info(
            "开始下载",
            f"共 {len(json_paths)} 个 JSON，请稍候…",
            duration=2000,
            parent=self.window(),
            position=InfoBarPosition.TOP,
        )

        self.worker = AudioDownloadWorker(
            json_paths=json_paths,
            output_dir=output_dir,
            options=options,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, value: int, message: str):
        self.progress_panel.update(value, message)

    def _on_finished(self, result: AudioDownloadResult):
        self.export_btn.setEnabled(True)
        self.progress_panel.finish()

        if result.success and not result.failed:
            InfoBar.success(
                "下载完成",
                f"成功导出 {len(result.success)} 个音频文件。",
                duration=4000,
                parent=self.window(),
                position=InfoBarPosition.TOP,
            )
            return

        lines = [f"成功: {len(result.success)} 个音频文件"]
        if result.failed:
            lines.append(f"失败: {len(result.failed)} 个 JSON 文件")
            for path, reason in result.failed[:8]:
                lines.append(f"- {os.path.basename(path)}: {reason}")

        box = MessageBox("下载结果", "\n".join(lines), self.window())
        box.yesButton.setText("确定")
        box.cancelButton.hide()
        box.exec()
