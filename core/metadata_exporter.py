"""从 MS JSON 批量提取曲目元数据、下载直链资源并生成 Excel。"""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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


@dataclass
class MetadataExportResult:
    excel_path: str
    success_count: int
    failed: list[tuple[str, str]]
    download_errors: list[str]


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


def _detect_image_suffix(data: bytes, content_type: str | None = None) -> str:
    """根据 Content-Type 或文件头魔数识别图片格式，返回固定扩展名。"""
    if content_type:
        normalized = content_type.split(";", 1)[0].strip().lower()
        for mime, suffix in (
            ("image/jpeg", ".jpg"),
            ("image/jpg", ".jpg"),
            ("image/png", ".png"),
            ("image/webp", ".webp"),
            ("image/gif", ".gif"),
            ("image/bmp", ".bmp"),
        ):
            if normalized == mime:
                return suffix

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


def _fetch_url(url: str) -> tuple[bytes, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "MS_json/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type")
    if not data:
        raise ValueError("下载的文件为空")
    return data, content_type


def _write_bytes_atomic(dest_path: Path, data: bytes) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        temp_path.write_bytes(data)
        os.replace(temp_path, dest_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


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


def _download_url_to_file(url: str, dest_path: Path) -> None:
    data, _ = _fetch_url(url)
    _write_bytes_atomic(dest_path, data)


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
) -> str:
    value = (url_or_path or "").strip()
    if not value:
        return ""

    dest_dir = output_dir / subfolder

    if value.startswith(("http://", "https://")):
        if field_name == "album_cover_path":
            try:
                data, content_type = _fetch_url(value)
            except urllib.error.URLError as exc:
                raise FileNotFoundError(f"下载失败: {value} ({exc})") from exc
            suffix = _detect_image_suffix(data, content_type)
            dest_path = dest_dir / f"{_resource_basename(mr_id, field_name)}{suffix}"
            _write_bytes_atomic(dest_path, data)
        else:
            suffix = _suffix_from_url(value, default_suffix)
            dest_path = dest_dir / f"{_resource_basename(mr_id, field_name)}{suffix}"
            try:
                _download_url_to_file(value, dest_path)
            except urllib.error.URLError as exc:
                raise FileNotFoundError(f"下载失败: {value} ({exc})") from exc
    else:
        suffix = _suffix_from_url(value, default_suffix)
        dest_path = dest_dir / f"{_resource_basename(mr_id, field_name)}{suffix}"
        source = _resolve_local_source(value, json_path)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest_path)

    return str(dest_path.relative_to(output_dir))


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

    for field_name, subfolder, default_suffix in RESOURCE_FIELDS:
        url = str(raw.get(field_name, "") or "").strip()
        resource_urls[field_name] = url
        resource_locals[field_name] = ""
        if not url or not download_resources:
            continue
        try:
            resource_locals[field_name] = _save_resource(
                url,
                json_path=song.source_path,
                output_dir=output_dir,
                subfolder=subfolder,
                mr_id=song.mr_id,
                default_suffix=default_suffix,
                field_name=field_name,
            )
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
    return SongMetadataRow(values=values, download_errors=errors)


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


def export_songs_metadata(
    json_paths: list[str],
    output_dir: str,
    *,
    download_resources: bool = True,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> MetadataExportResult:
    output_path = Path(output_dir)
    rows: list[list[str]] = []
    failed: list[tuple[str, str]] = []
    all_download_errors: list[str] = []

    for index, path in enumerate(json_paths, start=1):
        name = os.path.basename(path)
        if progress_callback is not None:
            progress_callback(index, len(json_paths), name)
        try:
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
            rows.append(row.values)
            for error in row.download_errors:
                all_download_errors.append(f"{name} ({song.mr_id}): {error}")
        except Exception as exc:
            failed.append((path, str(exc)))

    if not rows:
        raise ValueError("没有成功提取的曲目元数据")

    excel_path = write_metadata_excel(rows, output_dir)
    return MetadataExportResult(
        excel_path=excel_path,
        success_count=len(rows),
        failed=failed,
        download_errors=all_download_errors,
    )
