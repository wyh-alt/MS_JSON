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

from core.metadata_exporter import export_songs_metadata
from core.parser import collect_json_files
from ui.widgets import (
    BatchProgressPanel,
    DragLineEdit,
    PathLoader,
    ScrollableMessageBox,
)


@dataclass
class MetadataExportWorkerResult:
    excel_path: str | None = None
    success_count: int = 0
    failed: list[tuple[str, str]] | None = None
    download_errors: list[str] | None = None
    cache_hits: list[str] | None = None
    error: str | None = None


class MetadataExportWorker(QThread):
    progress = pyqtSignal(float, str)
    scan_progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)

    def __init__(
        self,
        input_path: str,
        output_dir: str,
        download_resources: bool,
        parent=None,
        json_paths: list | None = None,
    ):
        super().__init__(parent)
        self.input_path = input_path
        self.output_dir = output_dir
        self.download_resources = download_resources
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
                self.finished.emit(MetadataExportWorkerResult(error="未找到有效 JSON"))
                return

            def on_progress(index: int, total: int, name: str):
                # 兜底重试轮：进度保持 100%，消息带“重试: ”前缀
                if name.startswith("重试:"):
                    self.progress.emit(100, f"正在{name}")
                else:
                    self.progress.emit(index / total * 100, f"正在处理: {name}")

            result = export_songs_metadata(
                json_paths,
                self.output_dir,
                download_resources=self.download_resources,
                progress_callback=on_progress,
            )
            self.finished.emit(
                MetadataExportWorkerResult(
                    excel_path=result.excel_path,
                    success_count=result.success_count,
                    failed=result.failed,
                    download_errors=result.download_errors,
                    cache_hits=result.cache_hits,
                )
            )
        except Exception as exc:
            self.finished.emit(MetadataExportWorkerResult(error=str(exc)))


class MetadataExportPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("metadataExportPage")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("曲目元数据提取"))
        layout.addWidget(
            BodyLabel(
                "从 JSON 提取曲目元数据与曲速信息，下载专辑封面及 MR 音频直链到子文件夹，"
                "并生成 Excel 汇总表（不含歌词、MIDI、段落信息）。"
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
        option_layout.addWidget(StrongBodyLabel("提取选项"))
        self.download_checkbox = CheckBox("下载直链资源到子文件夹", option_card)
        self.download_checkbox.setChecked(True)
        self.download_checkbox.setToolTip(
            "将专辑封面、男调旋律、伴奏、鼓轨等直链分别保存到对应中文子文件夹。"
        )
        option_layout.addWidget(self.download_checkbox)
        layout.addWidget(option_card)

        output_card = CardWidget(container)
        output_layout = QVBoxLayout(output_card)
        output_layout.addWidget(StrongBodyLabel("输出目录"))
        output_layout.addWidget(
            BodyLabel(
                "将生成 曲目元数据.xlsx，资源按类型存入专辑封面、男调旋律等中文子文件夹。"
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

        self.worker: MetadataExportWorker | None = None

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
            InfoBar.warning("缺少输出目录", "请选择元数据与资源的输出目录。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return None
        return input_path, output_dir

    def _param_controls(self) -> list[QWidget]:
        """任务运行期间需要锁定的参数控件。"""
        return [
            self.input_edit,
            self.browse_input_btn,
            self.output_edit,
            self.browse_output_btn,
            self.download_checkbox,
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
        input_path, output_dir = paths
        download_resources = self.download_checkbox.isChecked()

        if self._path_loader is not None and self._path_loader.isRunning():
            # 预载入尚未完成：等待其结束再启动，避免二次加载导致进度跳回
            self._pending_start = True
            self._pending_params = dict(
                input_path=input_path,
                output_dir=output_dir,
                download_resources=download_resources,
            )
            self.export_btn.setEnabled(False)
            self._lock_params()
            self.progress_panel.start("等待加载JSON中 0%")
            return

        self._launch_worker(
            input_path=input_path,
            output_dir=output_dir,
            download_resources=download_resources,
        )

    def _launch_worker(self, **params):
        input_path = params["input_path"]
        self.export_btn.setEnabled(False)
        self._lock_params()
        self.progress_panel.start("正在扫描 JSON 文件…")
        InfoBar.info("开始提取", "正在扫描 JSON 文件，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        # 拖入路径时已预载入完成则直接复用，否则由 worker 自行扫描
        self.worker = MetadataExportWorker(
            input_path=input_path,
            output_dir=params["output_dir"],
            download_resources=params["download_resources"],
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

    def _on_progress(self, value: int, message: str):
        # 进入逐曲处理阶段，隐藏扫描进度圈
        self.input_edit.set_scan_progress(None)
        self.progress_panel.update(value, message)

    def _on_finished(self, result: MetadataExportWorkerResult):
        self.export_btn.setEnabled(True)
        self._unlock_params()
        self.input_edit.set_scan_progress(None)
        self.progress_panel.finish()

        if result.error:
            ScrollableMessageBox("提取失败", result.error, self.window()).exec()
            return

        failed = result.failed or []
        download_errors = result.download_errors or []
        cache_hits = result.cache_hits or []

        if not failed and not download_errors and not cache_hits:
            ScrollableMessageBox(
                "提取完成",
                f"已导出 {result.success_count} 首曲目元数据。\n{result.excel_path}",
                self.window(),
            ).exec()
            return

        lines = [
            f"成功: {result.success_count} 首",
            f"Excel: {result.excel_path}",
        ]
        if cache_hits:
            lines.append(cache_hits[0])
        if download_errors:
            lines.append(f"资源下载失败（已自动重试一次，仍失败）: {len(download_errors)} 项")
            lines.extend(f"- {item}" for item in download_errors)
        if failed:
            lines.append(f"JSON 失败（已自动重试一次，仍失败）: {len(failed)} 个")
            for path, reason in failed:
                lines.append(f"- {os.path.basename(path)}: {reason}")

        box = ScrollableMessageBox("提取结果", "\n".join(lines), self.window())
        box.exec()
