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
from ui.widgets import (
    BatchProgressPanel,
    DragLineEdit,
    PathLoader,
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
class LocalMidiResult:
    success: list[str]
    failed: list[tuple[str, str]]
    skipped: int
    calibration_notes: list[str]


class LocalMidiWorker(QThread):
    progress = pyqtSignal(float, str)
    scan_progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)

    def __init__(self, parent_dir, output_dir, part_mode, lyric_field, write_tempo, write_lyrics,
                 lyric_granularity, lower_octave, write_section_markers, exclude_rap_sections,
                 remove_non_melody_notes, time_offset_ms, audio_reference_calibration, parent=None,
                 bundles: list | None = None):
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
        self.bundles = bundles

    def _emit_scan_progress(self, index: int, total: int, name: str) -> None:
        """按整百分比节流，避免子文件夹过多时信号过密。"""
        if index == total or int(index / total * 10000) != int((index - 1) / total * 10000):
            self.scan_progress.emit(index, total)

    def run(self):
        if self.bundles is not None:
            # 拖入路径时已预载入完成，直接复用扫描结果
            bundles = self.bundles
        else:
            self.progress.emit(0, "正在扫描母文件夹…")
            try:
                bundles = scan_local_parent_dir(
                    self.parent_dir,
                    progress_callback=self._emit_scan_progress,
                )
            except ValueError as exc:
                self.finished.emit(LocalMidiResult(success=[], failed=[(self.parent_dir, str(exc))], skipped=0, calibration_notes=[]))
                return
        result = LocalMidiResult(success=[], failed=[], skipped=0, calibration_notes=[])
        total = len(bundles)
        for index, bundle in enumerate(bundles, start=1):
            name = os.path.basename(bundle.json_path)
            self.progress.emit(index / total * 100, f"正在处理: {name}")
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
        self.browse_input_btn = PushButton("浏览", input_card)
        self.browse_input_btn.clicked.connect(self._browse_input)
        input_row.addWidget(self.input_edit)
        input_row.addWidget(self.browse_input_btn)
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
        self.browse_output_btn = PushButton("浏览", output_card)
        self.browse_output_btn.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_edit)
        output_row.addWidget(self.browse_output_btn)
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

        # 拖入/输入路径后立即后台载入（扫描母文件夹），点击开始时直接复用结果
        self._path_loader: PathLoader | None = None
        self._loaded_result: tuple[str, list] | None = None
        self._pending_start = False
        self._pending_params: dict | None = None
        self.input_edit.textChanged.connect(self._on_input_path_changed)

    def _scan_loader(self, path, *, progress_callback=None, cancel_check=None):
        from core.local_resolver import scan_local_parent_dir

        return scan_local_parent_dir(
            path,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

    def _on_input_path_changed(self, path: str):
        path = (path or "").strip()
        # 路径变化时取消待启动任务（等待加载中重新拖入新目录需重新点击开始）
        self._pending_start = False
        self._pending_params = None
        if self._path_loader is not None and self._path_loader.isRunning():
            self._path_loader.cancel()
        self._path_loader = None
        self._loaded_result = None
        if not path or not os.path.isdir(path):
            self.input_edit.set_scan_progress(None)
            return
        # 载入过程中重新拖入新目录：取消旧载入并重新触发
        self.input_edit.set_scan_progress(0)
        loader = PathLoader(path, self._scan_loader, self)
        loader.scan_progress.connect(self._on_scan_progress)
        loader.finished.connect(self._on_path_loaded)
        self._path_loader = loader
        loader.start()

    def _on_path_loaded(self, payload):
        path, result, error = payload
        self._path_loader = None
        if path != self.input_edit.text().strip():
            return  # 载入期间路径已变化，丢弃过期结果
        self.input_edit.set_scan_progress(None)
        if error:
            self._loaded_result = None
        else:
            self._loaded_result = (path, result)
        if self._pending_start:
            # 点击开始时预载入未完成：载入结束后自动启动任务
            self._pending_start = False
            params = self._pending_params
            self._pending_params = None
            self._launch_worker(**params)

    def _loaded_bundles(self, input_path: str) -> list | None:
        """返回与当前输入路径匹配的预扫描结果，无则 None。"""
        if self._loaded_result is not None and self._loaded_result[0] == input_path:
            return self._loaded_result[1]
        return None

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

    def _param_controls(self) -> list[QWidget]:
        """任务运行期间需要锁定的参数控件。"""
        return [
            self.input_edit,
            self.browse_input_btn,
            self.output_edit,
            self.browse_output_btn,
            self.tempo_checkbox,
            self.lyrics_checkbox,
            self.lower_octave_checkbox,
            self.section_marker_checkbox,
            self.exclude_rap_checkbox,
            self.remove_non_melody_checkbox,
            self.part_combo,
            self.lyric_combo,
            self.lyric_granularity_combo,
            self.offset_spinbox,
            self.audio_calibration_checkbox,
        ]

    def _lock_params(self) -> None:
        """任务开始后锁定参数设置，防止运行中修改。"""
        self._param_enabled_states = {
            control: control.isEnabled() for control in self._param_controls()
        }
        for control in self._param_controls():
            control.setEnabled(False)

    def _unlock_params(self) -> None:
        """任务完成后按锁定前的可用状态恢复参数控件。"""
        for control, state in self._param_enabled_states.items():
            control.setEnabled(state)

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

        params = dict(
            parent_dir=parent_dir,
            output_dir=output_dir,
            part_mode=part_mode,
            lyric_field=lyric_field,
            write_tempo=write_tempo,
            write_lyrics=write_lyrics,
            lyric_granularity=lyric_granularity,
            lower_octave=lower_octave,
            write_section_markers=write_section_markers,
            exclude_rap_sections=exclude_rap_sections,
            remove_non_melody_notes=remove_non_melody_notes,
            time_offset_ms=time_offset_ms,
            audio_calibration=audio_calibration,
        )
        if self._path_loader is not None and self._path_loader.isRunning():
            # 预载入尚未完成：等待其结束再启动，避免二次加载导致进度跳回
            self._pending_start = True
            self._pending_params = params
            self.export_btn.setEnabled(False)
            self._lock_params()
            self.progress_panel.start("等待加载JSON中 0%")
            return

        self._launch_worker(**params)

    def _launch_worker(self, **params):
        parent_dir = params["parent_dir"]
        self.export_btn.setEnabled(False)
        self._lock_params()
        self.progress_panel.start("正在扫描母文件夹…")
        InfoBar.info("开始导出", "正在扫描母文件夹，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        self.worker = LocalMidiWorker(
            parent_dir, params["output_dir"], params["part_mode"], params["lyric_field"],
            params["write_tempo"], params["write_lyrics"],
            params["lyric_granularity"], params["lower_octave"],
            params["write_section_markers"], params["exclude_rap_sections"],
            params["remove_non_melody_notes"], params["time_offset_ms"],
            params["audio_calibration"],
            bundles=self._loaded_bundles(parent_dir),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.scan_progress.connect(self._on_scan_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_scan_progress(self, index: int, total: int) -> None:
        value = index / total * 100 if total else 100.0
        self.input_edit.set_scan_progress(value)
        if getattr(self, "_pending_start", False):
            # 等待加载阶段：主进度条保持不动，仅状态文字同步百分比
            self.progress_panel.status_label.setText(f"等待加载JSON中 {value:.2f}%")

    def _on_progress(self, value: int, message: str):
        # 进入逐曲处理阶段，隐藏扫描进度圈
        self.input_edit.set_scan_progress(None)
        self.progress_panel.update(value, message)

    def _on_finished(self, result: LocalMidiResult):
        self.export_btn.setEnabled(True)
        self._unlock_params()
        self.input_edit.set_scan_progress(None)
        self.progress_panel.finish()
        if result.success and not result.failed:
            detail = f"成功导出 {len(result.success)} 个 MIDI 文件。"
            if result.calibration_notes:
                detail += "\n音频校准:\n" + "\n".join(
                    f"- {note}" for note in result.calibration_notes
                )
            ScrollableMessageBox("导出完成", detail, self.window()).exec()
            return
        lines = [f"成功: {len(result.success)} 个 MIDI 文件"]
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
