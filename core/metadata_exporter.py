"""从 MS JSON 批量提取曲目元数据、下载直链资源并生成 Excel。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.audio_downloader import CACHE_DIR_NAME, download_cached_file
from core.parser import SongData, is_valid_ms_json, load_song_json

METADATA_EXCEL_NAME = "曲目元数据.xlsx"

METADATA_HEADERS: tuple[str, ...] = (
    "MSID",
    "原文歌名",
    "韩文歌名",
    "英文歌名",
    "原文歌手",
    "韩文歌手",
    "英文歌手",
    "原文专辑",
    "韩文专辑",
    "英文专辑",
    "原曲调性",
    "主曲速BPM",
    "曲速变化",
    "含罗马音",
    "罗马音版本",
    "JSON路径",
    "专辑封面直链",
    "专辑封面本地",
    "男调旋律直链",
    "男调旋律本地",
    "女调旋律直链",
    "女调旋律本地",
    "男调伴奏直链",
    "男调伴奏本地",
    "女调伴奏直链",
    "女调伴奏本地",
    "鼓轨直链",
    "鼓轨本地",
    "男调鼓轨直链",
    "男调鼓轨本地",
    "女调鼓轨直链",
    "女调鼓轨本地",
)

# (JSON 字段, 子文件夹名, 无后缀时的默认扩展名)
RESOURCE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("album_cover_path", "专辑封面", ".jpg"),
    ("file_mr_mel_m", "男调旋律", ".m4a"),
    ("file_mr_mel_w", "女调旋律", ".m4a"),
    ("file_mr_har_m", "男调伴奏", ".m4a"),
    ("file_mr_har_w", "女调伴奏", ".m4a"),
    ("file_mr_drum", "鼓轨", ".m4a"),
    ("file_mr_drum_m", "男调鼓轨", ".m4a"),
    ("file_mr_drum_w", "女调鼓轨", ".m4a"),
)


@dataclass
class SongMetadataRow:
    values: list[str]
    download_errors: list[str] = field(default_factory=list)
    cache_hits: list[str] = field(default_factory=list)


@dataclass
class MetadataExportResult:
    excel_path: str
    success_count: int
    failed: list[tuple[str, str]]
    download_errors: list[str]
    cache_hits: list[str] = field(default_factory=list)


def _resource_basename(mr_id: int, field_name: str) -> str:
    """音频类资源文件名（与音频下载命名一致）。"""
    mapping = {
        "file_mr_har_m": f"{mr_id}-m",
        "file_mr_har_w": f"{mr_id}-w",
        "file_mr_mel_m": f"{mr_id}-m-mel",
        "file_mr_mel_w": f"{mr_id}-w-mel",
        "file_mr_drum": f"{mr_id}-Drum",
        "file_mr_drum_m": f"{mr_id}-Drum",
        "file_mr_drum_w": f"{mr_id}-Drum",
    }
    return mapping.get(field_name, str(mr_id))


def _suffix_from_url(url: str, default: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    return suffix if suffix else default


def _detect_image_suffix(data: bytes) -> str:
    """根据文件头魔数识别图片格式，返回固定扩展名。"""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    return ".jpg"


def _format_tempos(tempos: list[dict[str, Any]]) -> str:
    if not tempos:
        return ""
    parts: list[str] = []
    for item in tempos:
        tempo = item.get("tempo")
        end_ms = item.get("end")
        if tempo is None:
            continue
        end_label = "" if end_ms in (None, "") else f"@{int(end_ms)}ms"
        parts.append(f"{float(tempo):.3f}{end_label}")
    return "; ".join(parts)


def _resolve_local_source(value: str, json_path: str) -> Path:
    path = Path(value)
    if path.is_file():
        return path
    by_name = Path(json_path).parent / path.name
    if by_name.is_file():
        return by_name
    raise FileNotFoundError(f"找不到本地文件: {value}")


def _save_resource(
    url_or_path: str,
    *,
    json_path: str,
    output_dir: Path,
    subfolder: str,
    mr_id: int,
    default_suffix: str,
    field_name: str = "",
) -> tuple[str, str | None]:
    """保存资源到输出目录，返回 (相对路径, 缓存命中消息或 None)。

    所有直链资源先下载到 JSON 原目录的共用缓存（.ms_json_audio_cache/），
    再从缓存拷贝到输出目录并重命名，保证音频下载、音频校准等模块可复用缓存。
    """
    value = (url_or_path or "").strip()
    if not value:
        return "", None

    dest_dir = output_dir / subfolder

    if value.startswith(("http://", "https://")):
        # 与音频下载/校准模块相同的缓存命名：sha256(url)[:32] + 后缀
        suffix = _suffix_from_url(value, default_suffix)
        cache_dir = Path(json_path).parent / CACHE_DIR_NAME
        cache_name = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32] + suffix
        cache_path = cache_dir / cache_name
        cache_hit = cache_path.is_file() and cache_path.stat().st_size > 0

        cache_path = download_cached_file(value, json_path, default_suffix)

        if field_name == "album_cover_path":
            # 封面真实格式以文件头魔数识别为准（URL 后缀可能缺失或不准确）
            suffix = _detect_image_suffix(cache_path.read_bytes())
        dest_path = dest_dir / f"{_resource_basename(mr_id, field_name)}{suffix}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache_path, dest_path)

        cache_hit_msg = (
            "已执行过音频下载或音频校准的曲目，元数据提取时会自动复用缓存，跳过重复下载"
            if cache_hit and field_name != "album_cover_path"
            else None
        )
        return str(dest_path.relative_to(output_dir)), cache_hit_msg
    else:
        suffix = _suffix_from_url(value, default_suffix)
        dest_path = dest_dir / f"{_resource_basename(mr_id, field_name)}{suffix}"
        source = _resolve_local_source(value, json_path)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest_path)
        return str(dest_path.relative_to(output_dir)), None


def build_metadata_row(
    song: SongData,
    raw: dict[str, Any],
    *,
    output_dir: Path,
    download_resources: bool,
) -> SongMetadataRow:
    mnote = raw.get("mnote") if isinstance(raw.get("mnote"), dict) else {}
    tempos = mnote.get("tempos", [])
    if not isinstance(tempos, list):
        tempos = []

    resource_urls: dict[str, str] = {}
    resource_locals: dict[str, str] = {}
    errors: list[str] = []

    cache_hits: list[str] = []
    for field_name, subfolder, default_suffix in RESOURCE_FIELDS:
        url = str(raw.get(field_name, "") or "").strip()
        resource_urls[field_name] = url
        resource_locals[field_name] = ""
        if not url or not download_resources:
            continue
        try:
            local_path, cache_msg = _save_resource(
                url,
                json_path=song.source_path,
                output_dir=output_dir,
                subfolder=subfolder,
                mr_id=song.mr_id,
                default_suffix=default_suffix,
                field_name=field_name,
            )
            resource_locals[field_name] = local_path
            if cache_msg:
                cache_hits.append(cache_msg)
        except Exception as exc:
            errors.append(f"{field_name}: {exc}")

    exists_rom = mnote.get("existsRom", "")
    rom_version = str(mnote.get("rom_translate_version", "") or "").strip()

    values = [
        str(song.mr_id),
        song.title_origin,
        song.title_ko,
        song.title_en,
        song.artist_origin,
        song.artist_ko,
        song.artist_en,
        song.album_origin,
        song.album_ko,
        song.album_en,
        song.original_key,
        f"{song.tempo_bpm:.3f}",
        _format_tempos(tempos),
        str(exists_rom),
        rom_version,
        song.source_path,
        resource_urls["album_cover_path"],
        resource_locals["album_cover_path"],
        resource_urls["file_mr_mel_m"],
        resource_locals["file_mr_mel_m"],
        resource_urls["file_mr_mel_w"],
        resource_locals["file_mr_mel_w"],
        resource_urls["file_mr_har_m"],
        resource_locals["file_mr_har_m"],
        resource_urls["file_mr_har_w"],
        resource_locals["file_mr_har_w"],
        resource_urls["file_mr_drum"],
        resource_locals["file_mr_drum"],
        resource_urls.get("file_mr_drum_m", ""),
        resource_locals.get("file_mr_drum_m", ""),
        resource_urls.get("file_mr_drum_w", ""),
        resource_locals.get("file_mr_drum_w", ""),
    ]
    return SongMetadataRow(values=values, download_errors=errors, cache_hits=cache_hits)


def write_metadata_excel(rows: list[list[str]], output_dir: str) -> str:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:
        raise RuntimeError("缺少 openpyxl 依赖，请先安装：pip install openpyxl") from exc

    if not rows:
        raise ValueError("没有可导出的曲目元数据")

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, METADATA_EXCEL_NAME)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "曲目元数据"
    sheet.append(list(METADATA_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        sheet.append(row)

    for column_cells in sheet.columns:
        max_length = 0
        column_letter = column_cells[0].column_letter
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(value))
        sheet.column_dimensions[column_letter].width = min(max(max_length + 2, 10), 56)

    workbook.save(output_path)
    return output_path


def cache_metadata_excel(excel_path: str, json_paths: list[str]) -> None:
    """把生成的 Excel 复制到各 JSON 原目录的共用缓存（.ms_json_audio_cache/）。

    与音频资源同缓存语义：按 JSON 父目录去重，每个目录的缓存中留存一份
    同名 Excel（覆盖旧副本），便于音频校准等模块直接复用；
    复制失败不影响主流程，静默跳过。
    """
    source = Path(excel_path)
    if not source.is_file():
        return
    seen_parents: set[str] = set()
    for path in json_paths:
        parent = str(Path(path).parent)
        if parent in seen_parents:
            continue
        seen_parents.add(parent)
        try:
            cache_dir = Path(parent) / CACHE_DIR_NAME
            cache_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, cache_dir / METADATA_EXCEL_NAME)
        except OSError:
            pass


MULTILANG_LYRIC_FIELDS = ("ko", "rom", "en")


def _export_multilang_lyrics(json_path: str, output_dir: Path) -> None:
    """以歌词导出模块默认设置导出韩文/罗马音/英文歌词，存入 歌词/ 子文件夹。

    三种语言共享一次音频校准（校准音频命中缓存，不重复下载）；
    命名与歌词导出模块一致（{mr_id}-{语言标签}.{扩展名}）。
    """
    from core.lyric_exporter import export_song_multilang_lyrics

    songs: dict[str, SongData] = {}
    for field in MULTILANG_LYRIC_FIELDS:
        songs[field] = load_song_json(json_path, lyric_field=field)
    export_song_multilang_lyrics(
        songs,
        str(output_dir / "歌词"),
        lyric_fields=MULTILANG_LYRIC_FIELDS,
        lyric_format="ksc-txt",
        part="all",
        title_lang="origin",
        artist_lang="origin",
        audio_reference_calibration=True,
    )


def _build_song_row(
    path: str,
    output_path: Path,
    download_resources: bool,
    export_lyrics: bool = False,
) -> tuple[SongData, SongMetadataRow]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not is_valid_ms_json(raw):
        raise ValueError("不是有效的 MS JSON 文件")
    song = load_song_json(path)
    row = build_metadata_row(
        song,
        raw,
        output_dir=output_path,
        download_resources=download_resources,
    )
    # 缓存/资源下载完成后，按歌词导出模块默认设置导出多语言歌词
    if export_lyrics:
        try:
            _export_multilang_lyrics(path, output_path)
        except Exception as exc:
            row.download_errors.append(f"歌词导出: {exc}")
    return song, row


def export_songs_metadata(
    json_paths: list[str],
    output_dir: str,
    *,
    download_resources: bool = True,
    export_lyrics: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> MetadataExportResult:
    """批量提取元数据并下载直链资源。

    第一轮逐首处理，遇到下载失败（链接超时等）的任务跳过并继续下一个；
    全部处理完后，对失败任务（含整首失败与资源下载失败但整首成功）做一次兜底重试，
    利用缓存只重新下载失败资源；重试后仍失败的任务列入最终 failed，由调用方弹窗列举。
    重试轮的 progress_callback 任务名带“重试: ”前缀。

    export_lyrics=True 时，在资源下载完成后按歌词导出模块默认设置，
    导出音频校准后的韩文/罗马音/英文歌词到输出目录的 歌词/ 子文件夹。
    """
    output_path = Path(output_dir)
    # path -> (mr_id, row)，重试成功后覆盖第一轮结果，保持 json_paths 顺序输出
    song_rows: dict[str, tuple[int, SongMetadataRow]] = {}
    # path -> 第一轮整首失败原因（重试成功则移除）
    pending: dict[str, str] = {}

    for index, path in enumerate(json_paths, start=1):
        name = os.path.basename(path)
        if progress_callback is not None:
            progress_callback(index, len(json_paths), name)
        try:
            song, row = _build_song_row(
                path, output_path, download_resources, export_lyrics
            )
            song_rows[path] = (song.mr_id, row)
        except Exception as exc:
            pending[path] = str(exc)

    # 兜底重试：整首失败 + 有资源下载错误的曲目（已成功资源命中缓存，只重下失败资源）
    retry_targets = [
        path
        for path in json_paths
        if path in pending
        or (path in song_rows and song_rows[path][1].download_errors)
    ]
    total_retry = len(retry_targets)
    for index, path in enumerate(retry_targets, start=1):
        name = os.path.basename(path)
        if progress_callback is not None:
            progress_callback(index, total_retry, f"重试: {name}")
        try:
            song, row = _build_song_row(
                path, output_path, download_resources, export_lyrics
            )
            song_rows[path] = (song.mr_id, row)
            pending.pop(path, None)
        except Exception as exc:
            pending[path] = str(exc)

    rows = [song_rows[path][1].values for path in json_paths if path in song_rows]
    failed = [(path, pending[path]) for path in json_paths if path in pending]

    if not rows:
        raise ValueError("没有成功提取的曲目元数据")

    all_download_errors = [
        f"{os.path.basename(path)} ({song_rows[path][0]}): {error}"
        for path in json_paths
        if path in song_rows
        for error in song_rows[path][1].download_errors
    ]
    all_cache_hits = [
        f"{os.path.basename(path)} ({song_rows[path][0]}): {msg}"
        for path in json_paths
        if path in song_rows
        for msg in song_rows[path][1].cache_hits
    ]

    excel_path = write_metadata_excel(rows, output_dir)
    # 在各 JSON 原目录的共用缓存中留存一份，供其他模块复用
    cache_metadata_excel(excel_path, json_paths)
    return MetadataExportResult(
        excel_path=excel_path,
        success_count=len(rows),
        failed=failed,
        download_errors=all_download_errors,
        cache_hits=all_cache_hits,
    )
