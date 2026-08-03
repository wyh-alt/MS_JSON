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

from core.lyric_exporter import (
    KscOptions,
    LYRIC_FORMAT_LABELS,
    LYRIC_PART_LABELS,
    META_LANG_LABELS,
    collect_section_export_rows,
    export_song_lyrics,
    write_sections_excel,
)
from core.parser import collect_json_files, load_song_json
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
class LyricExportResult:
    success: list[str]
    failed: list[tuple[str, str]]
    calibration_notes: list[str]


@dataclass
class SectionExportResult:
    output_path: str | None = None
    failed: list[tuple[str, str]] | None = None
    error: str | None = None


class SectionExportWorker(QThread):
    progress = pyqtSignal(float, str)
    scan_progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)

    def __init__(self, input_path, output_dir, lyric_field, title_lang, artist_lang,
                 time_offset_ms, audio_reference_calibration, parent=None,
                 json_paths: list | None = None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_dir = output_dir
        self.lyric_field = lyric_field
        self.title_lang = title_lang
        self.artist_lang = artist_lang
        self.time_offset_ms = time_offset_ms
        self.audio_reference_calibration = audio_reference_calibration
        self.json_paths = json_paths

    def _emit_scan_progress(self, index: int, total: int, name: str) -> None:
        """按整百分比节流，避免上万文件时信号过密。"""
        if index == total or int(index / total * 10000) != int((index - 1) / total * 10000):
            self.scan_progress.emit(index, total)

    def run(self):
        from core.parser import collect_json_files

        try:
            if self.json_paths is not None:
                # 拖入路径时已预载入完成，直接复用扫描结果
                json_paths = self.json_paths
            else:
                self.progress.emit(0, "正在扫描 JSON 文件…")
                json_paths = collect_json_files(
                    self.input_path,
                    valid_only=True,
                    progress_callback=self._emit_scan_progress,
                )
            if not json_paths:
                self.finished.emit(SectionExportResult(error="未找到有效 JSON"))
                return
            total = len(json_paths)
            all_rows = []
            for index, path in enumerate(json_paths, start=1):
                name = os.path.basename(path)
                self.progress.emit(index / total * 100, f"正在处理: {name}")
                song = load_song_json(path, self.lyric_field)
                all_rows.extend(
                    collect_section_export_rows(
                        song, title_lang=self.title_lang, artist_lang=self.artist_lang,
                        time_offset_ms=self.time_offset_ms,
                        audio_reference_calibration=self.audio_reference_calibration,
                    )
                )
            output_path = write_sections_excel(all_rows, self.output_dir)
            self.finished.emit(SectionExportResult(output_path=output_path))
        except Exception as exc:
            self.finished.emit(SectionExportResult(error=str(exc)))


class LyricExportWorker(QThread):
    progress = pyqtSignal(float, str)
    scan_progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)

    def __init__(self, input_path, output_dir, lyric_field, lyric_format, part,
                 title_lang, artist_lang, ksc_options, time_offset_ms,
                 audio_reference_calibration, parent=None,
                 json_paths: list | None = None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_dir = output_dir
        self.lyric_field = lyric_field
        self.lyric_format = lyric_format
        self.part = part
        self.title_lang = title_lang
        self.artist_lang = artist_lang
        self.ksc_options = ksc_options
        self.time_offset_ms = time_offset_ms
        self.audio_reference_calibration = audio_reference_calibration
        self.json_paths = json_paths

    def _emit_scan_progress(self, index: int, total: int, name: str) -> None:
        """按整百分比节流，避免上万文件时信号过密。"""
        if index == total or int(index / total * 10000) != int((index - 1) / total * 10000):
            self.scan_progress.emit(index, total)

    def run(self):
        from core.parser import collect_json_files

        if self.json_paths is not None:
            # 拖入路径时已预载入完成，直接复用扫描结果
            json_paths = self.json_paths
        else:
            self.progress.emit(0, "正在扫描 JSON 文件…")
            json_paths = collect_json_files(
                self.input_path,
                valid_only=True,
                progress_callback=self._emit_scan_progress,
            )

        result = LyricExportResult(success=[], failed=[], calibration_notes=[])
        if not json_paths:
            self.finished.emit(result)
            return
        total = len(json_paths)

        for index, path in enumerate(json_paths, start=1):
            name = os.path.basename(path)
            self.progress.emit(index / total * 100, f"正在处理: {name}")
            try:
                song = load_song_json(path, self.lyric_field)
                calibration_log: list[str] = []
                output_path = export_song_lyrics(
                    song,
                    self.output_dir,
                    lyric_format=self.lyric_format,
                    part=self.part,
                    lyric_field=self.lyric_field,
                    title_lang=self.title_lang,
                    artist_lang=self.artist_lang,
                    ksc_options=self.ksc_options,
                    time_offset_ms=self.time_offset_ms,
                    audio_reference_calibration=self.audio_reference_calibration,
                    calibration_log=calibration_log,
                )
                if calibration_log:
                    result.calibration_notes.append(f"{name}: {calibration_log[0]}")
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
        self.browse_input_btn = PushButton("浏览", input_card)
        self.browse_input_btn.clicked.connect(self._browse_input)
        input_row.addWidget(self.input_edit)
        input_row.addWidget(self.browse_input_btn)
        input_layout.addLayout(input_row)
        layout.addWidget(input_card)

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
            "根据 original_key 对应的 file_mr_mel 旋律音频，"
            "用能量包络检测首个可感知旋律音并与 MIDI 匹配；"
            "为全部歌词时间戳做整体偏移校准。"
        )
        offset_row.addWidget(self.audio_calibration_checkbox)
        offset_row.addStretch(1)
        option_layout.addLayout(offset_row)
        layout.addWidget(option_card)

        output_card = CardWidget(container)
        output_layout = QVBoxLayout(output_card)
        output_layout.addWidget(StrongBodyLabel("输出目录"))
        output_row = QHBoxLayout()
        self.output_edit = DragLineEdit(output_card)
        self.output_edit.setPlaceholderText("拖拽或选择歌词输出文件夹")
        self.browse_output_btn = PushButton("浏览", output_card)
        self.browse_output_btn.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_edit)
        output_row.addWidget(self.browse_output_btn)
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

        self.worker: LyricExportWorker | None = None
        self.section_worker: SectionExportWorker | None = None
        self._update_ksc_option_visibility()

        # 拖入/输入路径后立即后台载入（扫描校验 JSON），点击开始时直接复用结果
        self._path_loader: PathLoader | None = None
        self._loaded_result: tuple[str, list] | None = None
        self._pending_start = False
        self._pending_launcher = None
        self._pending_params: dict | None = None
        self.input_edit.textChanged.connect(self._on_input_path_changed)

    def _scan_loader(self, path, *, progress_callback=None, cancel_check=None):
        from core.parser import collect_json_files

        return collect_json_files(
            path,
            valid_only=True,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

    def _on_input_path_changed(self, path: str):
        path = (path or "").strip()
        # 路径变化时取消待启动任务（等待加载中重新拖入新目录需重新点击开始）
        self._pending_start = False
        self._pending_launcher = None
        self._pending_params = None
        if self._path_loader is not None and self._path_loader.isRunning():
            self._path_loader.cancel()
        self._path_loader = None
        self._loaded_result = None
        if not path or not os.path.exists(path):
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
            launcher = self._pending_launcher
            params = self._pending_params
            self._pending_launcher = None
            self._pending_params = None
            launcher(**params)

    def _loaded_json_paths(self, input_path: str) -> list | None:
        """返回与当前输入路径匹配的预扫描结果，无则 None。"""
        if self._loaded_result is not None and self._loaded_result[0] == input_path:
            return self._loaded_result[1]
        return None

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

    def _validate_inputs(self) -> tuple[str, str] | None:
        input_path = self.input_edit.text().strip()
        output_dir = self.output_edit.text().strip()
        if not input_path or not os.path.exists(input_path):
            InfoBar.warning("路径无效", "请输入或拖入有效的 JSON 文件/文件夹路径。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return None
        if not output_dir:
            InfoBar.warning("缺少输出目录", "请选择歌词文件的输出目录。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return None
        return input_path, output_dir

    def _param_controls(self) -> list[QWidget]:
        """任务运行期间需要锁定的参数控件。"""
        return [
            self.input_edit,
            self.browse_input_btn,
            self.output_edit,
            self.browse_output_btn,
            self.lyric_combo,
            self.part_combo,
            self.title_lang_combo,
            self.artist_lang_combo,
            self.format_combo,
            self.char_bracket_checkbox,
            self.word_bracket_checkbox,
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

    def _set_export_buttons_enabled(self, enabled: bool):
        self.export_btn.setEnabled(enabled)
        self.section_export_btn.setEnabled(enabled)
        if enabled:
            self.progress_panel.finish()

    def _start_section_export(self):
        paths = self._validate_inputs()
        if paths is None:
            return
        input_path, output_dir = paths
        lyric_field = LYRIC_FIELD_OPTIONS[self.lyric_combo.currentIndex()][1]
        title_lang = META_LANG_LABELS[self.title_lang_combo.currentIndex()][1]
        artist_lang = META_LANG_LABELS[self.artist_lang_combo.currentIndex()][1]
        time_offset_ms = self.offset_spinbox.value()
        audio_reference_calibration = self.audio_calibration_checkbox.isChecked()

        params = dict(
            input_path=input_path,
            output_dir=output_dir,
            lyric_field=lyric_field,
            title_lang=title_lang,
            artist_lang=artist_lang,
            time_offset_ms=time_offset_ms,
            audio_reference_calibration=audio_reference_calibration,
        )
        if self._path_loader is not None and self._path_loader.isRunning():
            # 预载入尚未完成：等待其结束再启动，避免二次加载导致进度跳回
            self._pending_start = True
            self._pending_launcher = self._launch_section_worker
            self._pending_params = params
            self._set_export_buttons_enabled(False)
            self._lock_params()
            self.progress_panel.start("等待加载JSON中 0%")
            return

        self._launch_section_worker(**params)

    def _launch_section_worker(self, **params):
        input_path = params["input_path"]
        self._set_export_buttons_enabled(False)
        self._lock_params()
        self.progress_panel.start("正在扫描 JSON 文件…")
        InfoBar.info("开始导出段落信息", "正在扫描 JSON 文件，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        self.section_worker = SectionExportWorker(
            input_path=input_path,
            output_dir=params["output_dir"],
            lyric_field=params["lyric_field"],
            title_lang=params["title_lang"],
            artist_lang=params["artist_lang"],
            time_offset_ms=params["time_offset_ms"],
            audio_reference_calibration=params["audio_reference_calibration"],
            json_paths=self._loaded_json_paths(input_path),
        )
        self.section_worker.progress.connect(self._on_section_progress)
        self.section_worker.scan_progress.connect(self._on_scan_progress)
        self.section_worker.finished.connect(self._on_section_finished)
        self.section_worker.start()

    def _on_scan_progress(self, index: int, total: int) -> None:
        value = index / total * 100 if total else 100.0
        self.input_edit.set_scan_progress(value)
        if getattr(self, "_pending_start", False):
            # 等待加载阶段：主进度条保持不动，仅状态文字同步百分比
            self.progress_panel.status_label.setText(f"等待加载JSON中 {value:.2f}%")

    def _on_section_progress(self, value: int, message: str):
        # 进入逐曲处理阶段，隐藏扫描进度圈
        self.input_edit.set_scan_progress(None)
        self.progress_panel.update(value, message)

    def _on_section_finished(self, result: SectionExportResult):
        self._set_export_buttons_enabled(True)
        self._unlock_params()
        self.input_edit.set_scan_progress(None)

        if result.error:
            ScrollableMessageBox("导出失败", result.error, self.window()).exec()
            return

        ScrollableMessageBox(
            "导出完成",
            f"段落信息已保存至：{result.output_path}",
            self.window(),
        ).exec()

    def _start_export(self):
        paths = self._validate_inputs()
        if paths is None:
            return
        input_path, output_dir = paths
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
        audio_reference_calibration = self.audio_calibration_checkbox.isChecked()

        params = dict(
            input_path=input_path,
            output_dir=output_dir,
            lyric_field=lyric_field,
            lyric_format=lyric_format,
            part=part,
            title_lang=title_lang,
            artist_lang=artist_lang,
            ksc_options=ksc_options,
            time_offset_ms=time_offset_ms,
            audio_reference_calibration=audio_reference_calibration,
        )
        if self._path_loader is not None and self._path_loader.isRunning():
            # 预载入尚未完成：等待其结束再启动，避免二次加载导致进度跳回
            self._pending_start = True
            self._pending_launcher = self._launch_worker
            self._pending_params = params
            self._set_export_buttons_enabled(False)
            self._lock_params()
            self.progress_panel.start("等待加载JSON中 0%")
            return

        self._launch_worker(**params)

    def _launch_worker(self, **params):
        input_path = params["input_path"]
        self._set_export_buttons_enabled(False)
        self._lock_params()
        self.progress_panel.start("正在扫描 JSON 文件…")
        InfoBar.info("开始导出", "正在扫描 JSON 文件，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        self.worker = LyricExportWorker(
            input_path=input_path,
            output_dir=params["output_dir"],
            lyric_field=params["lyric_field"],
            lyric_format=params["lyric_format"],
            part=params["part"],
            title_lang=params["title_lang"],
            artist_lang=params["artist_lang"],
            ksc_options=params["ksc_options"],
            time_offset_ms=params["time_offset_ms"],
            audio_reference_calibration=params["audio_reference_calibration"],
            json_paths=self._loaded_json_paths(input_path),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.scan_progress.connect(self._on_scan_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, value: int, message: str):
        # 进入逐曲处理阶段，隐藏扫描进度圈
        self.input_edit.set_scan_progress(None)
        self.progress_panel.update(value, message)

    def _on_finished(self, result: LyricExportResult):
        self._set_export_buttons_enabled(True)
        self._unlock_params()
        self.input_edit.set_scan_progress(None)

        if not result.success and not result.failed:
            ScrollableMessageBox(
                "未找到有效 JSON",
                "路径下没有包含 mnote 或 msi_melody_note 数据的有效 JSON 文件。",
                self.window(),
            ).exec()
            return

        if result.success and not result.failed:
            detail = f"成功导出 {len(result.success)} 个歌词文件。"
            if result.calibration_notes:
                detail += "\n音频校准:\n" + "\n".join(
                    f"- {note}" for note in result.calibration_notes
                )
            ScrollableMessageBox("导出完成", detail, self.window()).exec()
            return

        lines = [f"成功: {len(result.success)} 个歌词文件"]
        if result.calibration_notes:
            lines.append("音频校准:")
            lines.extend(f"- {note}" for note in result.calibration_notes)
        if result.failed:
            lines.append(f"失败: {len(result.failed)} 个 JSON 文件")
            for path, reason in result.failed:
                lines.append(f"- {os.path.basename(path)}: {reason}")

        box = ScrollableMessageBox("导出结果", "\n".join(lines), self.window())
        box.exec()
