"""交付资源一键提取：按勾选项目并行执行伴奏/MIDI/歌词/段落/交付总表导出。

各处理项目使用对应导出模块的默认参数；交付总表优先复用 JSON 原目录
共用缓存（.ms_json_audio_cache/）中已生成的元数据表格（MSID 覆盖校验），
无可用缓存时重新提取（不下载直链资源、不导歌词）。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.audio_downloader import (
    CACHE_DIR_NAME,
    AudioDownloadOptions,
    export_song_audio,
)
from core.lyric_exporter import (
    collect_section_export_rows,
    export_song_lyrics,
    write_sections_excel,
)
from core.metadata_exporter import METADATA_EXCEL_NAME, export_songs_metadata
from core.midi_exporter import export_song
from core.parser import load_song_json

# 项目标识与显示名（PROJECT_NAMES 亦用于日志框展示）
PROJECT_AUDIO = "audio"
PROJECT_MIDI = "midi"
PROJECT_LYRIC = "lyric"
PROJECT_SECTIONS = "sections"
PROJECT_METADATA = "metadata"

PROJECT_NAMES: dict[str, str] = {
    PROJECT_AUDIO: "伴奏处理",
    PROJECT_MIDI: "MIDI处理",
    PROJECT_LYRIC: "歌词处理",
    PROJECT_SECTIONS: "段落信息导出",
    PROJECT_METADATA: "交付总表导出",
}

# 交付总表最终文件名（需求④）
DELIVERY_METADATA_NAME = "资源产出交付总表.xlsx"
# 输出目录子文件夹（需求③）
SUBDIR_AUDIO = "合成伴奏"
SUBDIR_LYRIC = "歌词处理"
SUBDIR_MIDI = "MIDI处理"

# openpyxl 不支持并发保存同一文件，串行化两个 Excel 写入项目
_EXCEL_LOCK = threading.Lock()


@dataclass
class DeliveryProjectResult:
    name: str  # PROJECT_NAMES 中的中文名
    success: list[str] = field(default_factory=list)  # 输出文件绝对路径
    failed: list[tuple[str, str]] = field(default_factory=list)  # (json路径, 原因)；整项目失败时路径为 ""
    notes: list[str] = field(default_factory=list)  # 缓存复用/校准等说明


@dataclass
class DeliveryExportResult:
    projects: list[DeliveryProjectResult] = field(default_factory=list)
    error: str | None = None  # 编排层整体异常（扫描/启动阶段）


class _ProgressAggregator:
    """各项目并行报告自身进度，聚合为加权平均单一进度。

    各项目处理同一批 JSON，曲目数相同，故各项目等权。
    """

    def __init__(self, total: int, callback: Callable[[float, str], None] | None):
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}  # 每个项目最近一次完成比例 0..1
        self._total = total
        self._cb = callback

    def wrap(self, name: str) -> Callable[[float, str], None]:
        def report(fraction: float, message: str) -> None:
            with self._lock:
                self._last[name] = max(0.0, min(1.0, fraction))
                overall = sum(self._last.values()) / self._total
            if self._cb:
                self._cb(overall * 100.0, f"[{PROJECT_NAMES[name]}] {message}")

        return report


def _run_audio_project(
    json_paths: list[str],
    output_dir: str,
    progress_cb: Callable[[float, str], None],
) -> DeliveryProjectResult:
    """合并伴奏：按音频下载模块页面默认参数（merge_har_drum / original / wav / 44100，响度限幅分轨电平均开启）。"""
    result = DeliveryProjectResult(name=PROJECT_NAMES[PROJECT_AUDIO])
    options = AudioDownloadOptions(
        content="merge_har_drum",
        key_mode="original",
        output_format="wav",
        sample_rate=44100,
        # 默认立体声输出（与音频下载模块页面默认一致）
        channels="stereo",
        # 音频下载模块页面默认：响度 -12 LUFS、限幅 -1 dBTP、分轨电平 -6 dB 均勾选
        loudness_enabled=True,
        loudness_lufs=-12.0,
        limiter_enabled=True,
        limiter_db=-1.0,
        track_gain_enabled=True,
        track_gain_db=-6.0,
    )
    sub = Path(output_dir) / SUBDIR_AUDIO
    total = len(json_paths)
    for index, path in enumerate(json_paths, start=1):
        progress_cb(index / total, f"正在处理: {os.path.basename(path)}")
        try:
            song = load_song_json(path)
            out = export_song_audio(song, str(sub), options)
            result.success.append(out)
        except Exception as exc:
            result.failed.append((path, str(exc)))
    return result


def _run_midi_project(
    json_paths: list[str],
    output_dir: str,
    progress_cb: Callable[[float, str], None],
) -> DeliveryProjectResult:
    """MIDI：按 MIDI 导出模块默认参数（merge_same，校准开启）。"""
    result = DeliveryProjectResult(name=PROJECT_NAMES[PROJECT_MIDI])
    sub = Path(output_dir) / SUBDIR_MIDI
    total = len(json_paths)
    for index, path in enumerate(json_paths, start=1):
        progress_cb(index / total, f"正在处理: {os.path.basename(path)}")
        try:
            song = load_song_json(path, "ori")
            calibration_log: list[str] = []
            exported = export_song(
                song,
                str(sub),
                "merge_same",
                write_tempo=False,
                write_lyrics=True,
                lyric_granularity="word",
                lower_octave=True,
                write_section_markers=False,
                exclude_rap_sections=True,
                remove_non_melody_notes=True,
                time_offset_ms=0,
                audio_reference_calibration=True,
                calibration_log=calibration_log,
            )
            if calibration_log:
                result.notes.append(f"{os.path.basename(path)}: {calibration_log[0]}")
            result.success.extend(exported)
        except Exception as exc:
            result.failed.append((path, str(exc)))
    return result


def _run_lyric_project(
    json_paths: list[str],
    output_dir: str,
    progress_cb: Callable[[float, str], None],
) -> DeliveryProjectResult:
    """歌词：按歌词导出模块默认参数（ksc-txt / all / 原歌词，校准开启）。"""
    result = DeliveryProjectResult(name=PROJECT_NAMES[PROJECT_LYRIC])
    sub = Path(output_dir) / SUBDIR_LYRIC
    total = len(json_paths)
    for index, path in enumerate(json_paths, start=1):
        progress_cb(index / total, f"正在处理: {os.path.basename(path)}")
        try:
            song = load_song_json(path, "ori")
            calibration_log: list[str] = []
            out = export_song_lyrics(
                song,
                str(sub),
                lyric_format="ksc-txt",
                part="all",
                lyric_field="ori",
                title_lang="origin",
                artist_lang="origin",
                time_offset_ms=0,
                audio_reference_calibration=True,
                calibration_log=calibration_log,
            )
            if calibration_log:
                result.notes.append(f"{os.path.basename(path)}: {calibration_log[0]}")
            result.success.append(out)
        except Exception as exc:
            result.failed.append((path, str(exc)))
    return result


def _run_sections_project(
    json_paths: list[str],
    output_dir: str,
    progress_cb: Callable[[float, str], None],
) -> DeliveryProjectResult:
    """段落信息：校准开启，逐曲收集后写入 歌词段落信息及时间点.xlsx（输出目录根）。"""
    result = DeliveryProjectResult(name=PROJECT_NAMES[PROJECT_SECTIONS])
    all_rows: list[tuple[str, str, str, str, str, str, str]] = []
    total = len(json_paths)
    for index, path in enumerate(json_paths, start=1):
        progress_cb(index / total, f"正在处理: {os.path.basename(path)}")
        try:
            song = load_song_json(path, "ori")
            all_rows.extend(
                collect_section_export_rows(
                    song,
                    title_lang="origin",
                    artist_lang="origin",
                    time_offset_ms=0,
                    audio_reference_calibration=True,
                )
            )
        except Exception as exc:
            result.failed.append((path, str(exc)))
    if not all_rows:
        result.failed.append(("", "没有可导出的段落信息"))
        return result
    with _EXCEL_LOCK:
        out = write_sections_excel(all_rows, output_dir)
    result.success.append(out)
    result.notes.append(f"共 {len(all_rows)} 段")
    return result


def _collect_mr_ids(json_paths: list[str]) -> set[int]:
    """轻量提取输入 JSON 的 mr_id 集合（顶层字段，无需完整解析）。"""
    ids: set[int] = set()
    for path in json_paths:
        with open(path, "r", encoding="utf-8") as f:
            ids.add(int(json.load(f).get("mr_id", 0)))
    return ids


def _read_cached_metadata_msids(cache_table: Path) -> set[int]:
    """读取缓存元数据表第一列（MSID）集合，表头及非数字单元格跳过。"""
    from openpyxl import load_workbook

    wb = load_workbook(cache_table, read_only=True, data_only=True)
    try:
        ws = wb.active
        msids: set[int] = set()
        for row in ws.iter_rows(min_col=1, max_col=1, values_only=True):
            value = row[0]
            if value is None:
                continue
            try:
                msids.add(int(value))
            except (TypeError, ValueError):
                continue
        return msids
    finally:
        wb.close()


def _find_reusable_cache_table(json_paths: list[str], mr_ids: set[int]) -> Path | None:
    """按 JSON 父目录去重查找缓存表，任一表 MSID 集合覆盖全部输入即复用。"""
    needed = set(mr_ids)
    seen_parents: set[str] = set()
    for path in json_paths:
        parent = str(Path(path).parent)
        if parent in seen_parents:
            continue
        seen_parents.add(parent)
        cache_table = Path(parent) / CACHE_DIR_NAME / METADATA_EXCEL_NAME
        if not cache_table.is_file():
            continue
        try:
            cached = _read_cached_metadata_msids(cache_table)
        except Exception:
            continue
        if needed <= cached:
            return cache_table
    return None


def _run_metadata_project(
    json_paths: list[str],
    output_dir: str,
    progress_cb: Callable[[float, str], None],
) -> DeliveryProjectResult:
    """交付总表：缓存表可复用则拷贝重命名，否则重新提取（不下载直链、不导歌词）。"""
    result = DeliveryProjectResult(name=PROJECT_NAMES[PROJECT_METADATA])
    mr_ids = _collect_mr_ids(json_paths)

    # 分支 A：缓存可复用 → 直接拷贝并重命名
    cache_table = _find_reusable_cache_table(json_paths, mr_ids)
    if cache_table is not None:
        dest = Path(output_dir) / DELIVERY_METADATA_NAME
        cached_count = len(_read_cached_metadata_msids(cache_table))
        with _EXCEL_LOCK:
            shutil.copy2(cache_table, dest)
        result.success.append(str(dest))
        result.notes.append(
            f"命中缓存复用: {cache_table}（缓存 {cached_count} 首，"
            f"覆盖全部输入 {len(mr_ids)} 首）"
        )
        return result

    # 分支 B：重新提取（仅输出元数据表格；内部自动更新各目录缓存副本）
    def on_progress(index: int, total: int, name: str):
        if name.startswith("重试:"):
            progress_cb(1.0, f"重试: {name}")
        else:
            progress_cb(index / total, f"正在处理: {name}")

    with _EXCEL_LOCK:
        extracted = export_songs_metadata(
            json_paths,
            output_dir,
            download_resources=False,
            export_lyrics=False,
            progress_callback=on_progress,
        )
    final_path = os.path.join(output_dir, DELIVERY_METADATA_NAME)
    os.replace(extracted.excel_path, final_path)
    result.success.append(final_path)
    result.failed.extend(extracted.failed)
    result.notes.append(
        f"缓存缺失或不完整，重新提取（{len(mr_ids)} 首，未下载直链资源）"
    )
    return result


# 固定展示顺序：伴奏 → MIDI → 歌词 → 段落信息 → 交付总表
_PROJECT_FUNCS: dict[str, Callable[[list[str], str, Callable], DeliveryProjectResult]] = {
    PROJECT_AUDIO: _run_audio_project,
    PROJECT_MIDI: _run_midi_project,
    PROJECT_LYRIC: _run_lyric_project,
    PROJECT_SECTIONS: _run_sections_project,
    PROJECT_METADATA: _run_metadata_project,
}


def run_delivery_export(
    json_paths: list[str],
    output_dir: str,
    *,
    enabled: set[str],
    max_workers: int | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> DeliveryExportResult:
    """并行执行勾选的处理项目并输出到 output_dir。

    enabled 为 PROJECT_* 常量子集；各项目内部串行处理全部 JSON，
    项目之间并行（ThreadPoolExecutor，最多 5 个）。单个项目抛出的
    整体异常降级为该项目的失败记录，不影响其他项目。
    progress_callback(百分比 0-100, 状态文案) 由各项目进度加权聚合而来。
    """
    tasks = [(name, fn) for name, fn in _PROJECT_FUNCS.items() if name in enabled]
    if not tasks:
        return DeliveryExportResult(projects=[])

    aggregator = _ProgressAggregator(len(tasks), progress_callback)
    results: dict[str, DeliveryProjectResult] = {}
    worker_count = min(len(tasks), max_workers or 5)
    with ThreadPoolExecutor(
        max_workers=worker_count, thread_name_prefix="delivery"
    ) as pool:
        futures = {
            pool.submit(fn, json_paths, output_dir, aggregator.wrap(name)): name
            for name, fn in tasks
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                # 整项目失败降级，不影响其他项目
                results[name] = DeliveryProjectResult(
                    name=PROJECT_NAMES[name], failed=[("", str(exc))]
                )

    projects = [results[name] for name, _ in tasks]
    return DeliveryExportResult(projects=projects)
