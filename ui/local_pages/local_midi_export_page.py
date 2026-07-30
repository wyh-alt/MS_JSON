"""本地版 MIDI 导出页面：使用本地音频文件进行校准。"""
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
    MessageBox,
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
from core.midi_exporter import LYRIC_GRANULARITY_LABELS, PART_MODE_LABELS, export_song
from core.local_resolver import load_local_song_json
from ui.widgets import BatchProgressPanel, DragLineEdit, create_compact_combo, create_offset_spinbox

LYRIC_FIELD_OPTIONS = [
    ("原文歌词", "ori"),
    ("韩文歌词", "ko"),
    ("罗马音", "rom"),
    ("英文翻译", "en"),
]


@dataclass
class LocalMidiResult:
    success: list[str]
    failed: list[tuple[str, str]]
    skipped: int
    calibration_notes: list[str]


class LocalMidiWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(self, parent_dir, output_dir, part_mode, lyric_field, write_tempo, write_lyrics,
                 lyric_granularity, lower_octave, write_section_markers, exclude_rap_sections,
                 remove_non_melody_notes, time_offset_ms, audio_reference_calibration, parent=None):
        super().__init__(parent)
        self.parent_dir = parent_dir
        self.output_dir = output_dir
        self.part_mode = part_mode
        self.lyric_field = lyric_field
        self.write_tempo = write_tempo
        self.write_lyrics = write_lyrics
        self.lyric_granularity = lyric_granularity
        self.lower_octave = lower_octave
        self.write_section_markers = write_section_markers
        self.exclude_rap_sections = exclude_rap_sections
        self.remove_non_melody_notes = remove_non_melody_notes
        self.time_offset_ms = time_offset_ms
        self.audio_reference_calibration = audio_reference_calibration

    def run(self):
        self.progress.emit(0, "正在扫描母文件夹…")
        try:
            bundles = scan_local_parent_dir(self.parent_dir)
        except ValueError as exc:
            self.finished.emit(LocalMidiResult(success=[], failed=[(self.parent_dir, str(exc))], skipped=0, calibration_notes=[]))
            return
        result = LocalMidiResult(success=[], failed=[], skipped=0, calibration_notes=[])
        total = len(bundles)
        for index, bundle in enumerate(bundles, start=1):
            name = os.path.basename(bundle.json_path)
            self.progress.emit(int(index / total * 100), f"正在处理: {name}")
            try:
                song = load_local_song_json(bundle.json_path, self.lyric_field)
                song = populate_song_data_with_locals(song, bundle)
                calibration_log: list[str] = []
                exported = export_song(
                    song, self.output_dir, self.part_mode,
                    write_tempo=self.write_tempo, write_lyrics=self.write_lyrics,
                    lyric_granularity=self.lyric_granularity, lower_octave=self.lower_octave,
                    write_section_markers=self.write_section_markers,
                    exclude_rap_sections=self.exclude_rap_sections,
                    remove_non_melody_notes=self.remove_non_melody_notes,
                    time_offset_ms=self.time_offset_ms,
                    audio_reference_calibration=self.audio_reference_calibration,
                    calibration_log=calibration_log,
                )
                if calibration_log:
                    result.calibration_notes.append(f"{name}: {calibration_log[0]}")
                result.success.extend(exported)
            except Exception as exc:
                result.failed.append((bundle.json_path, str(exc)))
        self.finished.emit(result)


class LocalMidiExportPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("localMidiExportPage")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("MIDI 导出（本地版）"))
        layout.addWidget(
            BodyLabel("从母文件夹的各 MSID 子文件夹提取 MIDI，使用本地音频进行时间校准。")
        )

        # 输入
        input_card = CardWidget(container)
        input_layout = QVBoxLayout(input_card)
        input_layout.addWidget(StrongBodyLabel("母文件夹路径"))
        input_layout.addWidget(
            BodyLabel("选择包含多个 MSID 子文件夹的母文件夹，每个子文件夹应包含 JSON 与对应音频文件。")
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

        checkbox_row = QHBoxLayout()
        checkbox_row.setSpacing(12)
        self.tempo_checkbox = CheckBox("写入速度信息", option_card)
        self.tempo_checkbox.setChecked(False)
        self.lyrics_checkbox = CheckBox("写入歌词", option_card)
        self.lyrics_checkbox.setChecked(True)
        self.lyrics_checkbox.toggled.connect(self._update_lyric_option_visibility)
        self.lower_octave_checkbox = CheckBox("音符降低八度", option_card)
        self.lower_octave_checkbox.setChecked(True)
        self.section_marker_checkbox = CheckBox("写入段落标记", option_card)
        self.section_marker_checkbox.setChecked(False)
        self.exclude_rap_checkbox = CheckBox("删除Rap段落音符", option_card)
        self.exclude_rap_checkbox.setChecked(True)
        self.remove_non_melody_checkbox = CheckBox("删除疑似非旋律音符", option_card)
        self.remove_non_melody_checkbox.setChecked(True)
        checkbox_row.addWidget(self.tempo_checkbox)
        checkbox_row.addWidget(self.lyrics_checkbox)
        checkbox_row.addWidget(self.lower_octave_checkbox)
        checkbox_row.addWidget(self.section_marker_checkbox)
        checkbox_row.addWidget(self.exclude_rap_checkbox)
        checkbox_row.addWidget(self.remove_non_melody_checkbox)
        checkbox_row.addStretch(1)
        option_layout.addLayout(checkbox_row)

        part_row = QHBoxLayout()
        part_row.setSpacing(8)
        part_row.addWidget(BodyLabel("声部导出:"))
        self.part_combo = create_compact_combo(option_card, min_width=148, max_width=210)
        for label, _ in PART_MODE_LABELS:
            self.part_combo.addItem(label)
        part_row.addWidget(self.part_combo)
        part_row.addStretch(1)
        option_layout.addLayout(part_row)

        self.lyric_options_widget = QWidget(option_card)
        lyric_row = QHBoxLayout(self.lyric_options_widget)
        lyric_row.setContentsMargins(0, 0, 0, 0)
        lyric_row.setSpacing(8)
        lyric_row.addWidget(BodyLabel("歌词内容:", self.lyric_options_widget))
        self.lyric_combo = create_compact_combo(self.lyric_options_widget, min_width=96, max_width=120)
        for label, _ in LYRIC_FIELD_OPTIONS:
            self.lyric_combo.addItem(label)
        lyric_row.addWidget(self.lyric_combo)
        lyric_row.addWidget(BodyLabel("歌词粒度:", self.lyric_options_widget))
        self.lyric_granularity_combo = create_compact_combo(self.lyric_options_widget, min_width=168, max_width=228)
        for label, _ in LYRIC_GRANULARITY_LABELS:
            self.lyric_granularity_combo.addItem(label)
        lyric_row.addWidget(self.lyric_granularity_combo)
        lyric_row.addStretch(1)
        option_layout.addWidget(self.lyric_options_widget)
        self._update_lyric_option_visibility(self.lyrics_checkbox.isChecked())

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
        self.output_edit.setPlaceholderText("拖拽或选择 MIDI 输出文件夹")
        browse_output_btn = PushButton("浏览", output_card)
        browse_output_btn.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_edit)
        output_row.addWidget(browse_output_btn)
        output_layout.addLayout(output_row)
        layout.addWidget(output_card)

        self.progress_panel = BatchProgressPanel(container)
        layout.addWidget(self.progress_panel)

        action_row = QHBoxLayout()
        self.export_btn = PrimaryPushButton("开始导出", container)
        self.export_btn.clicked.connect(self._start_export)
        action_row.addStretch(1)
        action_row.addWidget(self.export_btn)
        layout.addLayout(action_row)
        layout.addStretch(1)

        self.worker: LocalMidiWorker | None = None

    def _update_lyric_option_visibility(self, checked: bool):
        self.lyric_options_widget.setVisible(checked)

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
            InfoBar.warning("缺少输出目录", "请选择 MIDI 文件的输出目录。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return None
        return parent_dir, output_dir

    def _start_export(self):
        paths = self._validate_inputs()
        if paths is None:
            return
        parent_dir, output_dir = paths
        part_mode = PART_MODE_LABELS[self.part_combo.currentIndex()][1]
        lyric_field = LYRIC_FIELD_OPTIONS[self.lyric_combo.currentIndex()][1]
        write_tempo = self.tempo_checkbox.isChecked()
        write_lyrics = self.lyrics_checkbox.isChecked()
        lyric_granularity = LYRIC_GRANULARITY_LABELS[self.lyric_granularity_combo.currentIndex()][1]
        lower_octave = self.lower_octave_checkbox.isChecked()
        write_section_markers = self.section_marker_checkbox.isChecked()
        exclude_rap_sections = self.exclude_rap_checkbox.isChecked()
        remove_non_melody_notes = self.remove_non_melody_checkbox.isChecked()
        time_offset_ms = self.offset_spinbox.value()
        audio_calibration = self.audio_calibration_checkbox.isChecked()

        self.export_btn.setEnabled(False)
        self.progress_panel.start("正在扫描母文件夹…")
        InfoBar.info("开始导出", "正在扫描母文件夹，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        self.worker = LocalMidiWorker(
            bundles, output_dir, part_mode, lyric_field, write_tempo, write_lyrics,
            lyric_granularity, lower_octave, write_section_markers, exclude_rap_sections,
            remove_non_melody_notes, time_offset_ms, audio_calibration,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, value: int, message: str):
        self.progress_panel.update(value, message)

    def _on_finished(self, result: LocalMidiResult):
        self.export_btn.setEnabled(True)
        self.progress_panel.finish()
        if result.success and not result.failed:
            detail = f"成功导出 {len(result.success)} 个 MIDI 文件。"
            if result.calibration_notes:
                detail += "\n" + "\n".join(result.calibration_notes[:5])
                if len(result.calibration_notes) > 5:
                    detail += f"\n... 另有 {len(result.calibration_notes) - 5} 条校准记录"
            InfoBar.success("导出完成", detail, duration=6000, parent=self.window(), position=InfoBarPosition.TOP)
            return
        lines = [f"成功: {len(result.success)} 个 MIDI 文件"]
        if result.calibration_notes:
            lines.append("音频校准:")
            lines.extend(f"- {note}" for note in result.calibration_notes[:8])
        if result.failed:
            lines.append(f"失败: {len(result.failed)} 个 MSID")
            for path, reason in result.failed[:8]:
                dir_name = os.path.basename(os.path.dirname(path))
                lines.append(f"- {dir_name}/{os.path.basename(path)}: {reason}")
        box = MessageBox("导出结果", "\n".join(lines), self.window())
        box.yesButton.setText("确定")
        box.cancelButton.hide()
        box.exec()
