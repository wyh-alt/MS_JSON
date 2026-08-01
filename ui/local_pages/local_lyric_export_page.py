"""本地版歌词导出页面：使用本地音频文件进行校准。"""
import os
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
)

from core.local_resolver import (
    LocalSongBundle,
    populate_song_data_with_locals,
    scan_local_parent_dir,
)
from core.lyric_exporter import (
    KscOptions,
    LYRIC_FORMAT_LABELS,
    LYRIC_PART_LABELS,
    META_LANG_LABELS,
    collect_section_export_rows,
    export_song_lyrics,
    write_sections_excel,
)
from core.local_resolver import load_local_song_json
from ui.widgets import (
    BatchProgressPanel,
    DragLineEdit,
    ScrollableMessageBox,
    create_compact_combo,
    create_offset_spinbox,
)

LYRIC_FIELD_OPTIONS = [
    ("原文歌词", "ori"),
    ("韩文歌词", "ko"),
    ("罗马音", "rom"),
    ("英文翻译", "en"),
]


@dataclass
class LocalLyricResult:
    success: list[str]
    failed: list[tuple[str, str]]
    calibration_notes: list[str]


@dataclass
class LocalSectionResult:
    output_path: str | None = None
    failed: list[tuple[str, str]] | None = None
    error: str | None = None


class LocalLyricWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(self, parent_dir, output_dir, lyric_field, lyric_format, part, title_lang, artist_lang,
                 ksc_options, time_offset_ms, audio_reference_calibration, parent=None):
        super().__init__(parent)
        self.parent_dir = parent_dir
        self.output_dir = output_dir
        self.lyric_field = lyric_field
        self.lyric_format = lyric_format
        self.part = part
        self.title_lang = title_lang
        self.artist_lang = artist_lang
        self.ksc_options = ksc_options
        self.time_offset_ms = time_offset_ms
        self.audio_reference_calibration = audio_reference_calibration

    def run(self):
        self.progress.emit(0, "正在扫描母文件夹…")
        try:
            bundles = scan_local_parent_dir(self.parent_dir)
        except ValueError as exc:
            self.finished.emit(LocalLyricResult(success=[], failed=[(self.parent_dir, str(exc))], calibration_notes=[]))
            return
        result = LocalLyricResult(success=[], failed=[], calibration_notes=[])
        total = len(bundles)
        for index, bundle in enumerate(bundles, start=1):
            name = os.path.basename(bundle.json_path)
            self.progress.emit(int(index / total * 100), f"正在处理: {name}")
            try:
                song = load_local_song_json(bundle.json_path, self.lyric_field)
                song = populate_song_data_with_locals(song, bundle)
                calibration_log: list[str] = []
                output_path = export_song_lyrics(
                    song, self.output_dir,
                    lyric_format=self.lyric_format, part=self.part,
                    lyric_field=self.lyric_field, title_lang=self.title_lang,
                    artist_lang=self.artist_lang, ksc_options=self.ksc_options,
                    time_offset_ms=self.time_offset_ms,
                    audio_reference_calibration=self.audio_reference_calibration,
                    calibration_log=calibration_log,
                )
                if calibration_log:
                    result.calibration_notes.append(f"{name}: {calibration_log[0]}")
                result.success.append(output_path)
            except Exception as exc:
                result.failed.append((bundle.json_path, str(exc)))
        self.finished.emit(result)


class LocalSectionWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(self, parent_dir, output_dir, lyric_field, title_lang, artist_lang,
                 time_offset_ms, audio_reference_calibration, parent=None):
        super().__init__(parent)
        self.parent_dir = parent_dir
        self.output_dir = output_dir
        self.lyric_field = lyric_field
        self.title_lang = title_lang
        self.artist_lang = artist_lang
        self.time_offset_ms = time_offset_ms
        self.audio_reference_calibration = audio_reference_calibration

    def run(self):
        self.progress.emit(0, "正在扫描母文件夹…")
        try:
            bundles = scan_local_parent_dir(self.parent_dir)
        except ValueError as exc:
            self.finished.emit(LocalSectionResult(error=str(exc)))
            return
        try:
            total = len(bundles)
            all_rows = []
            for index, bundle in enumerate(bundles, start=1):
                name = os.path.basename(bundle.json_path)
                self.progress.emit(int(index / total * 100), f"正在处理: {name}")
                song = load_local_song_json(bundle.json_path, self.lyric_field)
                song = populate_song_data_with_locals(song, bundle)
                all_rows.extend(
                    collect_section_export_rows(
                        song, title_lang=self.title_lang, artist_lang=self.artist_lang,
                        time_offset_ms=self.time_offset_ms,
                        audio_reference_calibration=self.audio_reference_calibration,
                    )
                )
            output_path = write_sections_excel(all_rows, self.output_dir)
            self.finished.emit(LocalSectionResult(output_path=output_path))
        except Exception as exc:
            self.finished.emit(LocalSectionResult(error=str(exc)))


class LocalLyricExportPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("localLyricExportPage")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("歌词导出（本地版）"))
        layout.addWidget(
            BodyLabel("从母文件夹的各 MSID 子文件夹提取歌词，使用本地音频进行时间校准。")
        )

        # 输入
        input_card = CardWidget(container)
        input_layout = QVBoxLayout(input_card)
        input_layout.addWidget(StrongBodyLabel("母文件夹路径"))
        input_layout.addWidget(
            BodyLabel("选择包含多个 MSID 子文件夹的母文件夹。")
        )
        input_row = QHBoxLayout()
        self.input_edit = DragLineEdit(input_card)
        self.input_edit.setPlaceholderText("拖拽或选择母文件夹路径")
        browse_input_btn = PushButton("浏览", input_card)
        browse_input_btn.clicked.connect(self._browse_input)
        input_row.addWidget(self.input_edit)
        input_row.addWidget(browse_input_btn)
        input_layout.addLayout(input_row)
        layout.addWidget(input_card)

        # 导出选项
        option_card = CardWidget(container)
        option_layout = QVBoxLayout(option_card)
        option_layout.setSpacing(8)
        option_layout.addWidget(StrongBodyLabel("导出选项"))

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(BodyLabel("歌词内容:"))
        self.lyric_combo = create_compact_combo(option_card, min_width=96, max_width=120)
        for label, _ in LYRIC_FIELD_OPTIONS:
            self.lyric_combo.addItem(label)
        row1.addWidget(self.lyric_combo)
        row1.addWidget(BodyLabel("声部:"))
        self.part_combo = create_compact_combo(option_card, min_width=108, max_width=140)
        for label, _ in LYRIC_PART_LABELS:
            self.part_combo.addItem(label)
        row1.addWidget(self.part_combo)
        row1.addWidget(BodyLabel("歌名:"))
        self.title_lang_combo = create_compact_combo(option_card, min_width=72, max_width=96)
        for label, _ in META_LANG_LABELS:
            self.title_lang_combo.addItem(label)
        row1.addWidget(self.title_lang_combo)
        row1.addWidget(BodyLabel("歌手:"))
        self.artist_lang_combo = create_compact_combo(option_card, min_width=72, max_width=96)
        for label, _ in META_LANG_LABELS:
            self.artist_lang_combo.addItem(label)
        row1.addWidget(self.artist_lang_combo)
        row1.addStretch(1)
        option_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(BodyLabel("歌词格式:"))
        self.format_combo = create_compact_combo(option_card, min_width=120, max_width=160)
        for label, _, _ in LYRIC_FORMAT_LABELS:
            self.format_combo.addItem(label)
        self.format_combo.currentIndexChanged.connect(self._update_ksc_option_visibility)
        row2.addWidget(self.format_combo)
        self.char_bracket_checkbox = CheckBox("字符中括号格式", option_card)
        self.char_bracket_checkbox.setChecked(True)
        self.word_bracket_checkbox = CheckBox("单词中括号格式", option_card)
        self.word_bracket_checkbox.setChecked(True)
        row2.addWidget(self.char_bracket_checkbox)
        row2.addWidget(self.word_bracket_checkbox)
        row2.addStretch(1)
        option_layout.addLayout(row2)
        self._ksc_option_widgets = [self.char_bracket_checkbox, self.word_bracket_checkbox]

        offset_row = QHBoxLayout()
        offset_row.setSpacing(8)
        offset_row.addWidget(BodyLabel("整体偏移:"))
        self.offset_spinbox = create_offset_spinbox(option_card)
        offset_row.addWidget(self.offset_spinbox)
        offset_row.addWidget(BodyLabel("正数向后，负数向前"))
        self.audio_calibration_checkbox = CheckBox("音频参考校准", option_card)
        self.audio_calibration_checkbox.setChecked(True)
        self.audio_calibration_checkbox.setToolTip(
            "根据本地旋律音频文件进行时间校准。"
        )
        offset_row.addWidget(self.audio_calibration_checkbox)
        offset_row.addStretch(1)
        option_layout.addLayout(offset_row)
        layout.addWidget(option_card)

        # 输出
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

        self.progress_panel = BatchProgressPanel(container)
        layout.addWidget(self.progress_panel)

        action_row = QHBoxLayout()
        self.section_export_btn = PushButton("导出段落信息", container)
        self.section_export_btn.clicked.connect(self._start_section_export)
        self.export_btn = PrimaryPushButton("开始导出", container)
        self.export_btn.clicked.connect(self._start_export)
        action_row.addStretch(1)
        action_row.addWidget(self.section_export_btn)
        action_row.addWidget(self.export_btn)
        layout.addLayout(action_row)
        layout.addStretch(1)

        self.lyric_worker: LocalLyricWorker | None = None
        self.section_worker: LocalSectionWorker | None = None
        self._update_ksc_option_visibility()

    def _current_lyric_format(self) -> str:
        return LYRIC_FORMAT_LABELS[self.format_combo.currentIndex()][1]

    def _update_ksc_option_visibility(self):
        is_ksc = self._current_lyric_format() in ("ksc-txt", "ksc")
        for widget in self._ksc_option_widgets:
            widget.setVisible(is_ksc)

    def _browse_input(self):
        folder = QFileDialog.getExistingDirectory(self, "选择母文件夹")
        if folder:
            self.input_edit.setText(folder)

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.output_edit.setText(folder)

    def _validate_inputs(self) -> tuple[str, str] | None:
        parent_dir = self.input_edit.text().strip()
        output_dir = self.output_edit.text().strip()
        if not parent_dir or not os.path.isdir(parent_dir):
            InfoBar.warning("路径无效", "请输入或拖入有效的母文件夹路径。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return None
        if not output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return None
        return parent_dir, output_dir

    def _set_buttons_enabled(self, enabled: bool):
        self.export_btn.setEnabled(enabled)
        self.section_export_btn.setEnabled(enabled)
        if enabled:
            self.progress_panel.finish()

    def _start_export(self):
        paths = self._validate_inputs()
        if paths is None:
            return
        parent_dir, output_dir = paths
        lyric_field = LYRIC_FIELD_OPTIONS[self.lyric_combo.currentIndex()][1]
        lyric_format = self._current_lyric_format()
        part = LYRIC_PART_LABELS[self.part_combo.currentIndex()][1]
        title_lang = META_LANG_LABELS[self.title_lang_combo.currentIndex()][1]
        artist_lang = META_LANG_LABELS[self.artist_lang_combo.currentIndex()][1]
        ksc_options = KscOptions(
            char_bracket=self.char_bracket_checkbox.isChecked(),
            word_bracket=self.word_bracket_checkbox.isChecked(),
        )
        time_offset_ms = self.offset_spinbox.value()
        audio_calibration = self.audio_calibration_checkbox.isChecked()

        self._set_buttons_enabled(False)
        self.progress_panel.start("正在扫描母文件夹…")
        InfoBar.info("开始导出", "正在扫描母文件夹，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        self.lyric_worker = LocalLyricWorker(
            bundles, output_dir, lyric_field, lyric_format, part, title_lang, artist_lang,
            ksc_options, time_offset_ms, audio_calibration,
        )
        self.lyric_worker.progress.connect(self._on_progress)
        self.lyric_worker.finished.connect(self._on_finished)
        self.lyric_worker.start()

    def _start_section_export(self):
        paths = self._validate_inputs()
        if paths is None:
            return
        parent_dir, output_dir = paths
        lyric_field = LYRIC_FIELD_OPTIONS[self.lyric_combo.currentIndex()][1]
        title_lang = META_LANG_LABELS[self.title_lang_combo.currentIndex()][1]
        artist_lang = META_LANG_LABELS[self.artist_lang_combo.currentIndex()][1]
        time_offset_ms = self.offset_spinbox.value()
        audio_calibration = self.audio_calibration_checkbox.isChecked()

        self._set_buttons_enabled(False)
        self.progress_panel.start("正在扫描母文件夹…")
        InfoBar.info("开始导出段落信息", "正在扫描母文件夹，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        self.section_worker = LocalSectionWorker(
            bundles, output_dir, lyric_field, title_lang, artist_lang, time_offset_ms, audio_calibration,
        )
        self.section_worker.progress.connect(self._on_section_progress)
        self.section_worker.finished.connect(self._on_section_finished)
        self.section_worker.start()

    def _on_progress(self, value: int, message: str):
        self.progress_panel.update(value, message)

    def _on_section_progress(self, value: int, message: str):
        self.progress_panel.update(value, message)

    def _on_finished(self, result: LocalLyricResult):
        self._set_buttons_enabled(True)
        if result.success and not result.failed:
            detail = f"成功导出 {len(result.success)} 个歌词文件。"
            if result.calibration_notes:
                detail += "\n音频校准:\n" + "\n".join(
                    f"- {note}" for note in result.calibration_notes
                )
            if len(result.calibration_notes) > 5:
                # 校准记录较多时用可滚动弹窗展示全部，便于核实
                ScrollableMessageBox("导出完成", detail, self.window()).exec()
            else:
                InfoBar.success("导出完成", detail, duration=6000, parent=self.window(), position=InfoBarPosition.TOP)
            return
        lines = [f"成功: {len(result.success)} 个歌词文件"]
        if result.calibration_notes:
            lines.append("音频校准:")
            lines.extend(f"- {note}" for note in result.calibration_notes)
        if result.failed:
            lines.append(f"失败: {len(result.failed)} 个 MSID")
            for path, reason in result.failed:
                dir_name = os.path.basename(os.path.dirname(path))
                lines.append(f"- {dir_name}/{os.path.basename(path)}: {reason}")
        box = ScrollableMessageBox("导出结果", "\n".join(lines), self.window())
        box.exec()

    def _on_section_finished(self, result: LocalSectionResult):
        self._set_buttons_enabled(True)
        if result.error:
            InfoBar.error("导出失败", result.error, duration=4000, parent=self.window(), position=InfoBarPosition.TOP)
            return
        InfoBar.success("导出完成", f"段落信息已保存至：{result.output_path}", duration=5000, parent=self.window(), position=InfoBarPosition.TOP)
