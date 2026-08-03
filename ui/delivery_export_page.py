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

from core.delivery_exporter import (
    PROJECT_AUDIO,
    PROJECT_LYRIC,
    PROJECT_METADATA,
    PROJECT_MIDI,
    PROJECT_SECTIONS,
    DeliveryExportResult,
)
from ui.widgets import (
    BatchProgressPanel,
    DragLineEdit,
    PathLoader,
    ScrollableMessageBox,
)

# 勾选项 -> (显示文本, 悬浮说明)
CHECKBOX_SPECS: dict[str, tuple[str, str]] = {
    PROJECT_AUDIO: (
        "伴奏处理",
        "合并伴奏（harmony+drum）按音频下载模块默认参数导出 WAV，存放至 合成伴奏/ 子文件夹。",
    ),
    PROJECT_MIDI: (
        "MIDI处理",
        "按 MIDI 导出模块默认参数导出 .mid，存放至 MIDI处理/ 子文件夹。",
    ),
    PROJECT_LYRIC: (
        "歌词处理",
        "按歌词导出模块默认参数导出原歌词（ksc-txt），存放至 歌词处理/ 子文件夹。",
    ),
    PROJECT_SECTIONS: (
        "段落信息导出",
        "生成 歌词段落信息及时间点.xlsx，直接放输出目录。",
    ),
    PROJECT_METADATA: (
        "交付总表导出",
        "生成 资源产出交付总表.xlsx；缓存表中 MSID 覆盖全部输入时直接复用，"
        "否则重新提取（不下载直链资源、不导歌词）。",
    ),
}

# 成功计数文案（按项目类型区分首/个文件/表格）
_SUCCESS_LABELS: dict[str, str] = {
    PROJECT_AUDIO: "首",
    PROJECT_MIDI: "个文件",
    PROJECT_LYRIC: "首",
    PROJECT_SECTIONS: "个表格",
    PROJECT_METADATA: "个表格",
}


class DeliveryExportWorker(QThread):
    progress = pyqtSignal(float, str)
    scan_progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)

    def __init__(
        self,
        input_path: str,
        output_dir: str,
        enabled: set[str],
        parent=None,
        json_paths: list | None = None,
    ):
        super().__init__(parent)
        self.input_path = input_path
        self.output_dir = output_dir
        self.enabled = enabled
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
                self.finished.emit(DeliveryExportResult(error="未找到有效 JSON"))
                return

            from core.delivery_exporter import run_delivery_export

            result = run_delivery_export(
                json_paths,
                self.output_dir,
                enabled=self.enabled,
                progress_callback=lambda value, message: self.progress.emit(value, message),
            )
            self.finished.emit(result)
        except Exception as exc:
            self.finished.emit(DeliveryExportResult(error=str(exc)))


class DeliveryExportPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deliveryExportPage")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("交付资源一键提取"))
        layout.addWidget(
            BodyLabel(
                "勾选处理内容后一键并行导出伴奏、MIDI、歌词、段落信息与交付总表，"
                "全部使用各模块默认参数。"
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
        option_layout.addWidget(StrongBodyLabel("处理内容"))
        # 全部勾选项一行排列
        self.checkboxes: dict[str, CheckBox] = {}
        checkbox_row = QHBoxLayout()
        checkbox_row.setSpacing(12)
        for project_key, (text, tooltip) in CHECKBOX_SPECS.items():
            checkbox = CheckBox(text, option_card)
            checkbox.setChecked(True)
            checkbox.setToolTip(tooltip)
            checkbox_row.addWidget(checkbox)
            self.checkboxes[project_key] = checkbox
        checkbox_row.addStretch(1)
        option_layout.addLayout(checkbox_row)
        layout.addWidget(option_card)

        output_card = CardWidget(container)
        output_layout = QVBoxLayout(output_card)
        output_layout.addWidget(StrongBodyLabel("输出目录"))
        output_layout.addWidget(
            BodyLabel(
                "伴奏/歌词/MIDI 按类型存入 合成伴奏/、歌词处理/、MIDI处理/ 子文件夹；"
                "段落信息表与交付总表直接放在输出目录。"
            )
        )
        output_row = QHBoxLayout()
        self.output_edit = DragLineEdit(output_card)
        self.output_edit.setPlaceholderText("拖拽或选择输出文件夹")
        self.browse_output_btn = PushButton("浏览", output_card)
        self.browse_output_btn.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_edit)
        output_row.addWidget(self.browse_output_btn)
        output_layout.addLayout(output_row)
        layout.addWidget(output_card)

        self.progress_panel = BatchProgressPanel(container)
        layout.addWidget(self.progress_panel)

        action_row = QHBoxLayout()
        self.export_btn = PrimaryPushButton("开始提取", container)
        self.export_btn.clicked.connect(self._start_export)
        action_row.addStretch(1)
        action_row.addWidget(self.export_btn)
        layout.addLayout(action_row)
        layout.addStretch(1)

        self.worker: DeliveryExportWorker | None = None

        # 拖入/输入路径后立即后台载入（扫描校验 JSON），点击开始时直接复用结果
        self._path_loader: PathLoader | None = None
        self._loaded_result: tuple[str, list] | None = None
        self._pending_start = False
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
            params = self._pending_params
            self._pending_params = None
            self._launch_worker(**params)

    def _loaded_json_paths(self, input_path: str) -> list | None:
        """返回与当前输入路径匹配的预扫描结果，无则 None。"""
        if self._loaded_result is not None and self._loaded_result[0] == input_path:
            return self._loaded_result[1]
        return None

    def _browse_input(self):
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
            InfoBar.warning("缺少输出目录", "请选择处理结果的输出目录。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return None
        return input_path, output_dir

    def _param_controls(self) -> list[QWidget]:
        """任务运行期间需要锁定的参数控件。"""
        return [
            self.input_edit,
            self.browse_input_btn,
            self.output_edit,
            self.browse_output_btn,
            *self.checkboxes.values(),
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

    def _selected_projects(self) -> set[str]:
        return {key for key, checkbox in self.checkboxes.items() if checkbox.isChecked()}

    def _start_export(self):
        paths = self._validate_inputs()
        if paths is None:
            return
        input_path, output_dir = paths
        enabled = self._selected_projects()
        if not enabled:
            InfoBar.warning("未勾选处理内容", "请至少勾选一项处理内容。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return

        if self._path_loader is not None and self._path_loader.isRunning():
            # 预载入尚未完成：等待其结束再启动，避免二次加载导致进度跳回
            self._pending_start = True
            self._pending_params = dict(
                input_path=input_path,
                output_dir=output_dir,
                enabled=enabled,
            )
            self.export_btn.setEnabled(False)
            self._lock_params()
            self.progress_panel.start("等待加载JSON中 0%")
            return

        self._launch_worker(
            input_path=input_path,
            output_dir=output_dir,
            enabled=enabled,
        )

    def _launch_worker(self, **params):
        input_path = params["input_path"]
        self.export_btn.setEnabled(False)
        self._lock_params()
        self.progress_panel.start("正在扫描 JSON 文件…")
        InfoBar.info("开始提取", "正在扫描 JSON 文件，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        # 拖入路径时已预载入完成则直接复用，否则由 worker 自行扫描
        self.worker = DeliveryExportWorker(
            input_path=input_path,
            output_dir=params["output_dir"],
            enabled=params["enabled"],
            json_paths=self._loaded_json_paths(input_path),
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

    def _on_progress(self, value: float, message: str):
        # 进入逐曲处理阶段，隐藏扫描进度圈
        self.input_edit.set_scan_progress(None)
        self.progress_panel.update(value, message)

    def _build_log_text(self, result: DeliveryExportResult) -> tuple[str, str]:
        """组装处理日志文本，返回 (弹窗标题, 正文)。"""
        all_failed = [item for p in result.projects for item in p.failed]
        title = "交付资源提取完成" if not all_failed else "交付资源提取结果"

        ok_count = sum(1 for p in result.projects if not p.failed and p.success)
        lines = [
            f"勾选 {len(result.projects)} 项，成功 {ok_count} 项。",
            f"输出目录: {self.output_edit.text().strip()}",
            "",
        ]
        for project in result.projects:
            lines.append(f"【{project.name}】")
            if project.success:
                label = _SUCCESS_LABELS.get(project.name, "项")
                lines.append(f"成功: {len(project.success)} {label}")
            if project.notes:
                lines.extend(f"- {note}" for note in project.notes)
            if project.failed:
                lines.append(f"失败: {len(project.failed)} 个")
                for path, reason in project.failed:
                    prefix = os.path.basename(path) if path else ""
                    lines.append(f"- {prefix}: {reason}" if prefix else f"- {reason}")
            if project.success:
                lines.append("文件:")
                lines.extend(f"- {path}" for path in project.success)
            lines.append("")
        return title, "\n".join(lines).rstrip()

    def _on_finished(self, result: DeliveryExportResult):
        self.export_btn.setEnabled(True)
        self._unlock_params()
        self.input_edit.set_scan_progress(None)
        self.progress_panel.finish()

        if result.error:
            ScrollableMessageBox("提取失败", result.error, self.window()).exec()
            return

        title, text = self._build_log_text(result)
        ScrollableMessageBox(title, text, self.window()).exec()
