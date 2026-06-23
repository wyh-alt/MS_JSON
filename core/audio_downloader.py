"""从 MS JSON 批量下载、混音并导出 MR 音频资源。"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.parser import SongData, resolve_album, resolve_artist, resolve_title

AudioContent = Literal[
    "merge_har_drum",
    "merge_har_drum_mel",
    "har",
    "mel",
    "drum",
]
KeyMode = Literal["original", "male", "female"]
OutputFormat = Literal["wav", "mp3", "m4a", "flac"]
M4aCodec = Literal["aac", "alac"]
NamingFormat = Literal["title-artist", "title-artist-album", "id-title-artist", "id"]

AUDIO_CONTENT_LABELS: list[tuple[str, AudioContent]] = [
    ("合并伴奏（harmony+drum）", "merge_har_drum"),
    ("合并伴奏（harmony+drum+melody）", "merge_har_drum_mel"),
    ("伴奏（harmony）", "har"),
    ("人声旋律（melody）", "mel"),
    ("鼓轨（Drum）", "drum"),
]

KEY_MODE_LABELS: list[tuple[str, KeyMode]] = [
    ("原始调性", "original"),
    ("男调", "male"),
    ("女调", "female"),
]

_KEY_MODE_DISPLAY = {mode: label for label, mode in KEY_MODE_LABELS}

OUTPUT_FORMAT_LABELS: list[tuple[str, OutputFormat]] = [
    ("WAV", "wav"),
    ("MP3", "mp3"),
    ("M4A", "m4a"),
    ("FLAC", "flac"),
]

SAMPLE_RATE_LABELS: list[tuple[str, int]] = [
    ("44100 Hz", 44100),
    ("48000 Hz", 48000),
]

PCM_BIT_DEPTH_LABELS: list[tuple[str, int]] = [
    ("16 Bit", 16),
    ("24 Bit", 24),
]

MP3_BITRATE_LABELS: list[tuple[str, int]] = [
    ("128 kbps", 128),
    ("192 kbps", 192),
    ("256 kbps", 256),
    ("320 kbps", 320),
]

M4A_BITRATE_LABELS: list[tuple[str, int]] = [
    ("128 kbps", 128),
    ("192 kbps", 192),
    ("256 kbps", 256),
    ("320 kbps", 320),
]

M4A_CODEC_LABELS: list[tuple[str, M4aCodec]] = [
    ("AAC", "aac"),
    ("ALAC", "alac"),
]

NAMING_FORMAT_LABELS: list[tuple[str, NamingFormat]] = [
    ("歌名-歌手", "title-artist"),
    ("歌名-歌手-专辑", "title-artist-album"),
    ("ID-歌名-歌手", "id-title-artist"),
    ("ID", "id"),
]

_TRACK_FIELD_NAMES = {
    "mel": ("file_mr_mel_m", "file_mr_mel_w"),
    "har": ("file_mr_har_m", "file_mr_har_w"),
    "drum": ("file_mr_drum_m", "file_mr_drum_w"),
}

_OPTIONAL_MERGE_TRACKS: dict[AudioContent, frozenset[str]] = {
    "merge_har_drum": frozenset({"drum"}),
    "merge_har_drum_mel": frozenset({"drum"}),
}

_REQUIRED_TRACKS: dict[AudioContent, frozenset[str]] = {
    "merge_har_drum": frozenset({"har"}),
    "merge_har_drum_mel": frozenset({"har", "mel"}),
    "har": frozenset({"har"}),
    "mel": frozenset({"mel"}),
    "drum": frozenset({"drum"}),
}

_TRACK_DISPLAY_NAMES = {
    "mel": "file_mr_mel",
    "har": "file_mr_har",
    "drum": "file_mr_drum",
}


@dataclass(frozen=True)
class AudioDownloadOptions:
    content: AudioContent
    key_mode: KeyMode
    output_format: OutputFormat
    sample_rate: int
    pcm_bit_depth: int = 16
    bitrate_kbps: int = 320
    m4a_codec: M4aCodec = "aac"
    naming_format: NamingFormat = "title-artist"
    title_lang: str = "origin"
    artist_lang: str = "origin"
    album_lang: str = "origin"


def resolve_key_suffix(key_mode: KeyMode, original_key: str) -> str:
    if key_mode == "male":
        return "m"
    if key_mode == "female":
        return "w"
    key = (original_key or "").strip().lower()
    if key in ("m", "w"):
        return key
    return "m"


def resolve_mr_track_url(song: SongData, track: str, key_mode: KeyMode) -> str | None:
    """按调性选择 file_mr_* 字段。"""
    suffix = resolve_key_suffix(key_mode, song.original_key)
    field_m, field_w = _TRACK_FIELD_NAMES[track]
    url = getattr(song, field_m if suffix == "m" else field_w, "") or ""
    url = str(url).strip()
    if track == "drum" and not url:
        url = (song.file_mr_drum or "").strip()
    return url or None


def resolve_audio_track_urls(
    song: SongData,
    content: AudioContent,
    key_mode: KeyMode,
) -> list[tuple[str, str]]:
    """返回 (轨道名, URL/路径) 列表，用于下载或混音。"""
    if content in _OPTIONAL_MERGE_TRACKS:
        candidate_tracks = ("har", "drum", "mel") if content == "merge_har_drum_mel" else ("har", "drum")
    else:
        candidate_tracks = tuple(_REQUIRED_TRACKS[content])

    required = _REQUIRED_TRACKS[content]

    resolved: list[tuple[str, str]] = []
    missing_required: list[str] = []

    for track in candidate_tracks:
        url = resolve_mr_track_url(song, track, key_mode)
        if url:
            resolved.append((track, url))
        elif track in required:
            missing_required.append(_TRACK_DISPLAY_NAMES[track])

    if missing_required:
        key_label = _KEY_MODE_DISPLAY[key_mode]
        raise ValueError(
            f"未找到所需音频字段（{', '.join(missing_required)}），调性: {key_label}"
        )
    if not resolved:
        key_label = _KEY_MODE_DISPLAY[key_mode]
        raise ValueError(f"未找到可导出的音频轨道，调性: {key_label}")
    return resolved


def resolve_mr_mel_url(song: SongData) -> str | None:
    """按 original_key 选择 file_mr_mel_m / file_mr_mel_w（供音频校准复用）。"""
    return resolve_mr_track_url(song, "mel", "original")


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name.strip() or "unknown"


def build_output_filename(song: SongData, options: AudioDownloadOptions) -> str:
    title = _sanitize_filename(resolve_title(song, options.title_lang))
    artist = _sanitize_filename(resolve_artist(song, options.artist_lang))
    album = _sanitize_filename(resolve_album(song, options.album_lang))

    if options.naming_format == "title-artist":
        base = f"{title}-{artist}"
    elif options.naming_format == "title-artist-album":
        base = f"{title}-{artist}-{album}" if album != "unknown" else f"{title}-{artist}"
    elif options.naming_format == "id-title-artist":
        base = f"{song.mr_id}-{title}-{artist}"
    else:
        base = str(song.mr_id)

    return f"{base}.{options.output_format}"


def resolve_audio_file(url_or_path: str, json_path: str) -> Path:
    value = (url_or_path or "").strip()
    if not value:
        raise FileNotFoundError("MR 音频路径为空")

    if value.startswith(("http://", "https://")):
        return download_cached_audio(value, json_path)

    path = Path(value)
    if path.is_file():
        return path

    json_dir = Path(json_path).parent
    by_name = json_dir / path.name
    if by_name.is_file():
        return by_name

    raise FileNotFoundError(f"找不到 MR 音频: {value}")


def download_cached_audio(url: str, json_path: str) -> Path:
    suffix = Path(url.split("?", 1)[0]).suffix or ".m4a"
    cache_dir = Path(json_path).parent / ".ms_json_audio_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + suffix
    cache_path = cache_dir / cache_name

    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path

    temp_path = cache_path.with_suffix(cache_path.suffix + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "MS_json/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        if not data:
            raise ValueError("下载的音频文件为空")
        temp_path.write_bytes(data)
        os.replace(temp_path, cache_path)
    except urllib.error.URLError as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise FileNotFoundError(f"下载 MR 音频失败: {url} ({exc})") from exc
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

    return cache_path


def find_ffmpeg_executable() -> str | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "ffmpeg.exe")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "ffmpeg.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def _pcm_codec(bit_depth: int) -> str:
    return "pcm_s16le" if bit_depth == 16 else "pcm_s24le"


def _build_ffmpeg_output_args(options: AudioDownloadOptions) -> list[str]:
    args = ["-ar", str(options.sample_rate)]
    fmt = options.output_format
    if fmt == "wav":
        args.extend(["-c:a", _pcm_codec(options.pcm_bit_depth)])
    elif fmt == "mp3":
        args.extend(["-c:a", "libmp3lame", "-b:a", f"{options.bitrate_kbps}k"])
    elif fmt == "m4a":
        if options.m4a_codec == "alac":
            args.extend(["-c:a", "alac"])
        else:
            args.extend(["-c:a", "aac", "-b:a", f"{options.bitrate_kbps}k"])
    elif fmt == "flac":
        args.extend(["-c:a", "flac"])
        if options.pcm_bit_depth == 16:
            args.extend(["-sample_fmt", "s16"])
        else:
            args.extend(["-sample_fmt", "s32", "-bits_per_raw_sample", "24"])
    return args


def _run_ffmpeg(args: list[str]) -> None:
    ffmpeg = find_ffmpeg_executable()
    if ffmpeg is None:
        raise RuntimeError("未找到 ffmpeg，请安装 ffmpeg 或将其放入程序目录")

    try:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", *args],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg 处理失败: {stderr or exc}") from exc


def _export_single_source(source: Path, output_path: Path, options: AudioDownloadOptions) -> None:
    _run_ffmpeg(
        [
            "-y",
            "-i",
            str(source),
            *_build_ffmpeg_output_args(options),
            str(output_path),
        ]
    )


def _mix_and_export(sources: list[Path], output_path: Path, options: AudioDownloadOptions) -> None:
    input_args: list[str] = []
    for source in sources:
        input_args.extend(["-i", str(source)])

    filter_inputs = "".join(f"[{index}:a]" for index in range(len(sources)))
    filter_complex = (
        f"{filter_inputs}amix=inputs={len(sources)}"
        ":duration=longest:dropout_transition=0:normalize=0[aout]"
    )

    _run_ffmpeg(
        [
            "-y",
            *input_args,
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            *_build_ffmpeg_output_args(options),
            str(output_path),
        ]
    )


def export_song_audio(
    song: SongData,
    output_dir: str,
    options: AudioDownloadOptions,
) -> str:
    """下载并导出单首歌曲的 MR 音频，返回输出文件路径。"""
    track_urls = resolve_audio_track_urls(song, options.content, options.key_mode)
    local_sources = [
        resolve_audio_file(url, song.source_path) for _, url in track_urls
    ]

    os.makedirs(output_dir, exist_ok=True)
    filename = build_output_filename(song, options)
    output_path = Path(output_dir) / filename

    if len(local_sources) == 1:
        _export_single_source(local_sources[0], output_path, options)
    else:
        _mix_and_export(local_sources, output_path, options)

    return str(output_path)
