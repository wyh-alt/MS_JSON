"""本地 MS JSON 资源扫描与匹配模块。

从包含多个 MSID 子文件夹的母文件夹中，自动发现每个子文件夹内的
JSON 文件与对应音频/封面资源，按文件命名规则匹配后注入 SongData。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import json as _json
import tempfile

from core.parser import SongData, is_valid_ms_json, load_song_json

# ---------------------------------------------------------------------------
# 音频文件命名规则（10 位补零 mr_id）
# ---------------------------------------------------------------------------
# 0000000028-mr-harmony-m_ff32d819.m4a
# 0000009944-mr-melody-m_7a3e8c53.m4a
# 0000009944-mr-drum_7a3e8c53.m4a

_AUDIO_EXTENSIONS = (".m4a", ".wav", ".mp3", ".flac", ".aac", ".ogg")

AUDIO_PATTERNS: dict[str, re.Pattern] = {
    "har_m":  re.compile(r"^\d{10}-mr-harmony-m_[a-f0-9]+\.(?:m4a|wav|mp3|flac|aac|ogg)$", re.IGNORECASE),
    "har_w":  re.compile(r"^\d{10}-mr-harmony-w_[a-f0-9]+\.(?:m4a|wav|mp3|flac|aac|ogg)$", re.IGNORECASE),
    "mel_m":  re.compile(r"^\d{10}-mr-melody-m_[a-f0-9]+\.(?:m4a|wav|mp3|flac|aac|ogg)$", re.IGNORECASE),
    "mel_w":  re.compile(r"^\d{10}-mr-melody-w_[a-f0-9]+\.(?:m4a|wav|mp3|flac|aac|ogg)$", re.IGNORECASE),
    "drum":   re.compile(r"^\d{10}-mr-drum_[a-f0-9]+\.(?:m4a|wav|mp3|flac|aac|ogg)$", re.IGNORECASE),
    "drum_m": re.compile(r"^\d{10}-mr-drum-m_[a-f0-9]+\.(?:m4a|wav|mp3|flac|aac|ogg)$", re.IGNORECASE),
    "drum_w": re.compile(r"^\d{10}-mr-drum-w_[a-f0-9]+\.(?:m4a|wav|mp3|flac|aac|ogg)$", re.IGNORECASE),
}

# 专辑封面：{10位mr_id}_{hash}.{ext}  例：0000000955_477b6ab8.jpg
COVER_PATTERN = re.compile(
    r"^\d{10}_[a-f0-9]+\.(?:jpg|jpeg|png|webp|gif|bmp)$", re.IGNORECASE
)

# 从 JSON 的 RESOURCE_FIELDS 映射
TRACK_TO_SONG_FIELD: dict[str, str] = {
    "har_m":  "file_mr_har_m",
    "har_w":  "file_mr_har_w",
    "mel_m":  "file_mr_mel_m",
    "mel_w":  "file_mr_mel_w",
    "drum":   "file_mr_drum",
    "drum_m": "file_mr_drum_m",
    "drum_w": "file_mr_drum_w",
}

# metadata_exporter 的 RESOURCE_FIELDS 对应的 track key
_FIELD_TO_TRACK: dict[str, str] = {
    "album_cover_path": "cover",
    "file_mr_mel_m": "mel_m",
    "file_mr_mel_w": "mel_w",
    "file_mr_har_m": "har_m",
    "file_mr_har_w": "har_w",
    "file_mr_drum": "drum",
    "file_mr_drum_m": "drum_m",
    "file_mr_drum_w": "drum_w",
}


def _is_valid_local_ms_json(data: dict[str, Any]) -> bool:
    """兼容 mnote 和 msi_melody_note 两种 JSON 格式的校验。"""
    mnote = data.get("mnote") or data.get("msi_melody_note")
    if not isinstance(mnote, dict):
        return False
    return isinstance(mnote.get("note"), list) and isinstance(mnote.get("section"), list)


def load_local_song_json(path: str, lyric_field: str = "ori") -> SongData:
    """加载本地 JSON 并兼容 msi_melody_note 与 mnote 两种顶层键名。

    若 JSON 使用 msi_melody_note，先将其重命名为 mnote 后写入临时文件，
    再交由原版 load_song_json 解析。对标准 mnote 格式直接透传。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = _json.load(f)

    if "msi_melody_note" in data and "mnote" not in data:
        data["mnote"] = data.pop("msi_melody_note")
        fd, tmp = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                _json.dump(data, fh)
            return load_song_json(tmp, lyric_field)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    return load_song_json(path, lyric_field)


def _is_hidden(name: str) -> bool:
    """名称以 . 开头视为隐藏文件/文件夹。"""
    return name.startswith(".")


@dataclass
class LocalSongBundle:
    """一个 MSID 子文件夹的完整资源包。"""
    msid: int
    subfolder: str                     # 子文件夹绝对路径
    json_path: str                     # JSON 文件绝对路径
    audio_map: dict[str, str] = field(default_factory=dict)   # {track_key: 本地路径}
    cover_path: str | None = None      # 专辑封面路径，可为 None


def _match_audio_files(subfolder: str) -> dict[str, str]:
    """在子文件夹内匹配音频文件，返回 {track_key: 绝对路径}。"""
    result: dict[str, str] = {}
    try:
        entries = os.listdir(subfolder)
    except OSError:
        return result

    for name in entries:
        if _is_hidden(name):
            continue
        full = os.path.join(subfolder, name)
        if not os.path.isfile(full):
            continue
        for track_key, pattern in AUDIO_PATTERNS.items():
            if pattern.match(name):
                # 同名文件多条轨道匹配时，靠后的模式覆盖（如 drum_m 优于 drum）
                result[track_key] = full
                break

    # drum 作为 drum_m / drum_w 的兜底
    if "drum" in result:
        result.setdefault("drum_m", result["drum"])
        result.setdefault("drum_w", result["drum"])
    return result


def _match_cover(subfolder: str) -> str | None:
    """在子文件夹内匹配专辑封面，返回绝对路径或 None。"""
    try:
        entries = os.listdir(subfolder)
    except OSError:
        return None

    for name in entries:
        if _is_hidden(name):
            continue
        full = os.path.join(subfolder, name)
        if not os.path.isfile(full):
            continue
        if COVER_PATTERN.match(name):
            return full
    return None


def _find_json(subfolder: str) -> str | None:
    """在子文件夹内查找第一个有效的 MS JSON 文件。

    优先返回有效 MS JSON，否则回退到第一个 .json 文件。
    """
    json_files = _collect_all_json_files(subfolder)
    if not json_files:
        return None

    for path in json_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if _is_valid_local_ms_json(data):
                return path
        except Exception:
            continue
    return json_files[0]


def _extract_msid_from_folder(folder_name: str) -> int | None:
    """从文件夹名尝试提取 msid（纯数字或 10 位补零）。"""
    try:
        return int(folder_name)
    except ValueError:
        return None


def _extract_msid_from_filename(filename: str) -> int | None:
    """从 JSON 文件名提取 msid（前 10 位数字）。"""
    match = re.match(r"^(\d+)", filename)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def _collect_all_json_files(subfolder: str) -> list[str]:
    """收集子文件夹内所有非隐藏 JSON 文件（按名称排序）。"""
    try:
        entries = sorted(os.listdir(subfolder))
    except OSError:
        return []
    return [
        os.path.join(subfolder, name)
        for name in entries
        if not _is_hidden(name) and name.lower().endswith(".json") and os.path.isfile(os.path.join(subfolder, name))
    ]


def _find_json_by_msid(subfolder: str, expected_msid: int) -> str | None:
    """根据预期的 msid 查找匹配的 JSON 文件。

    优先按文件名 msid 匹配有效 MS JSON，次优取任意有效 MS JSON，
    最后回退到子文件夹内第一个 JSON 文件（含非 MS 格式）。
    """
    all_json = _collect_all_json_files(subfolder)
    if not all_json:
        return None

    # 按文件名中的 msid 与预期 msid 的差值排序
    candidates: list[tuple[int, str]] = []
    for full in all_json:
        msid_from_name = _extract_msid_from_filename(os.path.basename(full))
        if msid_from_name is not None:
            candidates.append((abs(msid_from_name - expected_msid), full))

    # 非 msid 命名的 JSON 排在最后
    for full in all_json:
        if not any(full == c[1] for c in candidates):
            candidates.append((999999, full))

    candidates.sort(key=lambda x: x[0])

    # 第一轮：优先返回有效 MS JSON
    for _, path in candidates:
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if is_valid_ms_json(data):
                return path
        except Exception:
            continue

    # 兜底：返回第一个 JSON 文件（即使不是标准 MS 格式）
    return all_json[0]


def scan_local_parent_dir(
    parent_dir: str,
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[LocalSongBundle]:
    """扫描母文件夹，返回所有有效 MSID 子文件夹资源包。

    每个子文件夹应包含：
    - 一个有效的 MS JSON 文件
    - 零个或多个音频文件（按命名规则匹配）
    - 零个或一个封面图片

    忽略以 . 开头的文件和文件夹。
    progress_callback(index, total, name) 在每个子文件夹读取时回调，用于展示扫描进度；
    cancel_check() 返回 True 时中断扫描（用于路径变更后取消旧载入）。
    """
    parent = os.path.abspath(parent_dir)
    if not os.path.isdir(parent):
        raise ValueError(f"母文件夹不存在或不是目录: {parent}")

    bundles: list[LocalSongBundle] = []
    try:
        entries = sorted(os.listdir(parent))
    except OSError as exc:
        raise ValueError(f"无法读取母文件夹: {parent} ({exc})") from exc

    candidates = [
        name
        for name in entries
        if not _is_hidden(name) and os.path.isdir(os.path.join(parent, name))
    ]
    total = len(candidates)
    for index, name in enumerate(candidates, start=1):
        if cancel_check is not None and cancel_check():
            break
        if progress_callback is not None:
            progress_callback(index, total, name)
        subfolder = os.path.join(parent, name)

        # 尝试从文件夹名提取 msid
        msid = _extract_msid_from_folder(name)
        if msid is not None:
            json_path = _find_json_by_msid(subfolder, msid)
        else:
            json_path = _find_json(subfolder)

        if json_path is None:
            continue

        # 加载 JSON 获取准确的 mr_id
        try:
            import json
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            actual_msid = int(data.get("mr_id", 0))
        except Exception:
            continue

        audio_map = _match_audio_files(subfolder)
        cover_path = _match_cover(subfolder)

        bundles.append(
            LocalSongBundle(
                msid=actual_msid,
                subfolder=subfolder,
                json_path=json_path,
                audio_map=audio_map,
                cover_path=cover_path,
            )
        )

    if not bundles:
        raise ValueError(f"母文件夹中未找到有效的 MS JSON 子文件夹: {parent}")

    bundles.sort(key=lambda b: b.msid)
    return bundles


def populate_song_data_with_locals(song: SongData, bundle: LocalSongBundle) -> SongData:
    """将 LocalSongBundle 中的本地音频路径填入 SongData 的 file_mr_* 字段。

    返回修改后的 SongData（原地修改并返回同一对象，方便链式调用）。
    """
    for track_key, field_name in TRACK_TO_SONG_FIELD.items():
        local_path = bundle.audio_map.get(track_key, "")
        if local_path and not getattr(song, field_name, ""):
            setattr(song, field_name, local_path)
    return song


def inject_local_paths_to_raw(raw: dict[str, Any], bundle: LocalSongBundle) -> dict[str, Any]:
    """将本地音频/封面路径注入 JSON raw 字典的资源字段。

    仅注入 JSON 中原本为空的字段，已有值的字段保持原样（如有 URL 则保留 URL）。
    返回修改后的同一字典。
    """
    for field_name, track_key in _FIELD_TO_TRACK.items():
        existing = str(raw.get(field_name, "") or "").strip()
        if existing:
            continue
        if track_key == "cover":
            if bundle.cover_path:
                raw[field_name] = bundle.cover_path
        else:
            local_path = bundle.audio_map.get(track_key, "")
            if local_path:
                raw[field_name] = local_path
    return raw
