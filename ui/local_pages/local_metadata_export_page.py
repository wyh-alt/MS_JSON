"""本地版元数据提取页面：从本地子文件夹复制资源替代直链下载。"""
import json
import os
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
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
    inject_local_paths_to_raw,
    scan_local_parent_dir,
)
from core.metadata_exporter import (
    build_metadata_row,
    is_valid_ms_json,
    write_metadata_excel,
)
from core.local_resolver import load_local_song_json
from ui.widgets import BatchProgressPanel, DragLineEdit, ScrollableMessageBox

# 直链（URL）列的索引（0-based）：偶数索引 16..30
_URL_COLUMN_INDICES = {16, 18, 20, 22, 24, 26, 28, 30}


@dataclass
class LocalMetadataResult:
    excel_path: str | None = None
    success_count: int = 0
    failed: list[tuple[str, str]] | None = None
    resource_errors: list[str] | None = None
    cache_hits: list[str] | None = None
    error: str | None = None


class LocalMetadataWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(self, parent_dir: str, output_dir: str, parent=None):
        super().__init__(parent)
        self.parent_dir = parent_dir
        self.output_dir = output_dir

    def run(self):
        from core.parser import is_valid_ms_json as _is_valid
        self.progress.emit(0, "正在扫描母文件夹…")
        try:
            bundles = scan_local_parent_dir(self.parent_dir)
        except ValueError as exc:
            self.finished.emit(LocalMetadataResult(error=str(exc)))
            return

        try:
            from pathlib import Path as _Path

            output_path = _Path(self.output_dir)
            rows: list[list[str]] = []
            all_resource_errors: list[str] = []
            all_cache_hits: list[str] = []
            failed: list[tuple[str, str]] = []

            for index, bundle in enumerate(bundles, start=1):
                name = os.path.basename(bundle.json_path)
                self.progress.emit(int(index / len(bundles) * 100), f"正在处理: {name}")
                try:
                    with open(bundle.json_path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if not is_valid_ms_json(raw):
                        raise ValueError("不是有效的 MS JSON 文件")
                    raw = inject_local_paths_to_raw(raw, bundle)
                    song = load_local_song_json(bundle.json_path)
                    row = build_metadata_row(
                        song,
                        raw,
                        output_dir=output_path,
                        download_resources=True,
                    )
                    # 清除直链列（本地版本无 URL）
                    values = list(row.values)
                    for idx in _URL_COLUMN_INDICES:
                        if idx < len(values):
                            values[idx] = ""
                    rows.append(values)
                    for error in row.download_errors:
                        all_resource_errors.append(f"{name} ({song.mr_id}): {error}")
                    for msg in row.cache_hits:
                        all_cache_hits.append(f"{name} ({song.mr_id}): {msg}")
                except Exception as exc:
                    failed.append((bundle.json_path, str(exc)))

            if not rows:
                raise ValueError("没有成功提取的曲目元数据")

            excel_path = write_metadata_excel(rows, self.output_dir)
            self.finished.emit(
                LocalMetadataResult(
                    excel_path=excel_path,
                    success_count=len(rows),
                    failed=failed,
                    resource_errors=all_resource_errors,
                    cache_hits=all_cache_hits,
                )
            )
        except Exception as exc:
            self.finished.emit(LocalMetadataResult(error=str(exc)))


class LocalMetadataExportPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("localMetadataExportPage")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("曲目元数据提取（本地版）"))
        layout.addWidget(
            BodyLabel(
                "从母文件夹的各 MSID 子文件夹中提取 JSON 元数据与本地音频/封面资源，"
                "生成 Excel 汇总表（不含歌词、MIDI、段落信息）。"
            )
        )

        # 输入：母文件夹
        input_card = CardWidget(container)
        input_layout = QVBoxLayout(input_card)
        input_layout.addWidget(StrongBodyLabel("母文件夹路径"))
        input_layout.addWidget(
            BodyLabel("选择包含多个 MSID 子文件夹的母文件夹，每个子文件夹应包含 JSON 与对应资源文件。")
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

        # 输出目录
        output_card = CardWidget(container)
        output_layout = QVBoxLayout(output_card)
        output_layout.addWidget(StrongBodyLabel("输出目录"))
        output_layout.addWidget(
            BodyLabel("将生成 曲目元数据.xlsx，资源按类型存入专辑封面、男调旋律等中文子文件夹。")
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

        self.worker: LocalMetadataWorker | None = None

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
            InfoBar.warning("缺少输出目录", "请选择元数据与资源的输出目录。", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
            return None
        return parent_dir, output_dir

    def _param_controls(self) -> list[QWidget]:
        """任务运行期间需要锁定的参数控件。"""
        return [
            self.input_edit,
            self.browse_input_btn,
            self.output_edit,
            self.browse_output_btn,
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

        self.export_btn.setEnabled(False)
        self._lock_params()
        self.progress_panel.start("正在扫描母文件夹…")
        InfoBar.info("开始提取", "正在扫描母文件夹，请稍候…", duration=2000, parent=self.window(), position=InfoBarPosition.TOP)

        self.worker = LocalMetadataWorker(parent_dir, output_dir)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, value: int, message: str):
        self.progress_panel.update(value, message)

    def _on_finished(self, result: LocalMetadataResult):
        self.export_btn.setEnabled(True)
        self._unlock_params()
        self.progress_panel.finish()
        if result.error:
            ScrollableMessageBox("提取失败", result.error, self.window()).exec()
            return

        failed = result.failed or []
        resource_errors = result.resource_errors or []
        cache_hits = result.cache_hits or []
        if not failed and not resource_errors and not cache_hits:
            ScrollableMessageBox(
                "提取完成",
                f"已导出 {result.success_count} 首曲目元数据。\n{result.excel_path}",
                self.window(),
            ).exec()
            return

        lines = [f"成功: {result.success_count} 首", f"Excel: {result.excel_path}"]
        if cache_hits:
            lines.append(cache_hits[0])
        if resource_errors:
            lines.append(f"资源问题: {len(resource_errors)} 项")
            lines.extend(f"- {item}" for item in resource_errors)
        if failed:
            lines.append(f"失败: {len(failed)} 个")
            for path, reason in failed:
                dir_name = os.path.basename(os.path.dirname(path))
                lines.append(f"- {dir_name}/{os.path.basename(path)}: {reason}")
        box = ScrollableMessageBox("提取结果", "\n".join(lines), self.window())
        box.exec()
