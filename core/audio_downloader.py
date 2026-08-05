"""从 MS JSON 批量下载、混音并导出 MR 音频资源。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.parser import SongData

# Windows 下调用 ffmpeg 时抑制一闪而过的控制台窗口
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# JSON 原目录下的共用资源缓存：元数据提取、音频下载、音频校准等模块共用
CACHE_DIR_NAME = ".ms_json_audio_cache"

# 多模块并行时（如交付资源一键提取）可能并发下载同一 URL，
# .part 临时文件 + os.replace 需串行化避免竞态
_DOWNLOAD_LOCK = threading.Lock()

# 第一遍只用来测量，LRA 取多少都不影响 measured_* 结果
_MEASURE_LRA = 11.0

# alimiter 是样本域限幅器，配合 N 倍过采样近似真峰限幅。4x 已能把真峰
# 摁到目标附近 0.1 dB 内（音乐内容实测），CPU 成本可控。
LIMITER_OVERSAMPLE = 4

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
Channels = Literal["stereo", "mono", "source"]

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

CHANNEL_LABELS: list[tuple[str, Channels]] = [
    ("立体声", "stereo"),
    ("单声道", "mono"),
    ("与源相同", "source"),
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
    channels: Channels = "stereo"
    pcm_bit_depth: int = 16
    bitrate_kbps: int = 320
    m4a_codec: M4aCodec = "aac"
    loudness_enabled: bool = False
    loudness_lufs: float = -12.0
    limiter_enabled: bool = False
    limiter_db: float = -1.0
    track_gain_enabled: bool = True
    track_gain_db: float = -6.0


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
    key_suffix = resolve_key_suffix(options.key_mode, song.original_key)

    if options.content == "merge_har_drum":
        base = f"{song.mr_id}-完整伴奏"
    elif options.content == "merge_har_drum_mel":
        base = f"{song.mr_id}-完整伴奏-mel"
    elif options.content == "har":
        base = f"{song.mr_id}-{key_suffix}"
    elif options.content == "mel":
        base = f"{song.mr_id}-{key_suffix}-mel"
    elif options.content == "drum":
        base = f"{song.mr_id}-Drum"
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


def download_cached_file(url: str, json_path: str, default_suffix: str = ".m4a") -> Path:
    """下载任意 URL 资源到 JSON 原目录缓存（.ms_json_audio_cache/），返回缓存路径。

    所有模块共用同一缓存：元数据提取、音频下载、音频校准等先落缓存再取用，
    命中的缓存文件直接复用，跳过重复下载。缓存名 = sha256(url)[:32] + 后缀。
    """
    suffix = Path(url.split("?", 1)[0]).suffix or default_suffix
    cache_dir = Path(json_path).parent / CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + suffix
    cache_path = cache_dir / cache_name

    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path

    # 并发调用方可能同时下载同一 URL，串行化下载与落盘避免 .part 竞态
    with _DOWNLOAD_LOCK:
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return cache_path  # 等待锁期间其他线程已完成下载

        temp_path = cache_path.with_suffix(cache_path.suffix + ".part")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MS_json/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
            if not data:
                raise ValueError("下载的文件为空")
            temp_path.write_bytes(data)
            os.replace(temp_path, cache_path)
        except urllib.error.URLError as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise FileNotFoundError(f"下载失败: {url} ({exc})") from exc
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    return cache_path


def download_cached_audio(url: str, json_path: str) -> Path:
    return download_cached_file(url, json_path, ".m4a")


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


@dataclass(frozen=True)
class LoudnessMeasurement:
    input_i: str
    input_lra: str
    input_tp: str
    input_thresh: str
    target_offset: str


def _normalize_negative(value: float) -> float:
    return value if value < 0 else -abs(value)


def _resolve_loudness_params(options: AudioDownloadOptions) -> tuple[float | None, float | None]:
    """返回 (loudness_lufs, limiter_db)，未启用者为 None。"""
    limit_db = _normalize_negative(options.limiter_db) if options.limiter_enabled else None
    target_lufs = _normalize_negative(options.loudness_lufs) if options.loudness_enabled else None
    return target_lufs, limit_db


def _resolve_track_gain_db(options: AudioDownloadOptions) -> float | None:
    """仅在合并伴奏且勾选分轨电平时返回增益值，其他场景为 None。"""
    if not options.track_gain_enabled:
        return None
    return float(options.track_gain_db)


def _channel_layout_filter(channels: Channels) -> str | None:
    """声道转换 filter；「与源相同」时返回 None（输出保持源声道数）。"""
    if channels in ("stereo", "mono"):
        return f"aformat=channel_layouts={channels}"
    return None


def _build_amix_expr(count: int, track_gain_db: float | None = None) -> str:
    if track_gain_db is not None:
        pre = "".join(
            f"[{i}:a]volume={track_gain_db}dB[t{i}];" for i in range(count)
        )
        filter_inputs = "".join(f"[t{i}]" for i in range(count))
    else:
        pre = ""
        filter_inputs = "".join(f"[{i}:a]" for i in range(count))
    return (
        f"{pre}"
        f"{filter_inputs}amix=inputs={count}"
        f":duration=longest:dropout_transition=0:normalize=0"
    )


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


def _invoke_ffmpeg(args: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    ffmpeg = find_ffmpeg_executable()
    if ffmpeg is None:
        raise RuntimeError("未找到 ffmpeg，请安装 ffmpeg 或将其放入程序目录")

    loglevel = "error" if quiet else "info"
    try:
        return subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", loglevel, *args],
            check=True,
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg 处理失败: {stderr or exc}") from exc


def _run_ffmpeg(args: list[str]) -> None:
    _invoke_ffmpeg(args, quiet=True)


def _parse_loudness_json(stderr_text: str) -> LoudnessMeasurement:
    start = stderr_text.rfind("{")
    end = stderr_text.rfind("}")
    if start < 0 or end < 0 or end < start:
        tail = stderr_text[-500:]
        raise RuntimeError(f"loudnorm 分析未返回 JSON 结果: {tail}")
    try:
        stats = json.loads(stderr_text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"loudnorm JSON 解析失败: {exc}") from exc
    try:
        return LoudnessMeasurement(
            input_i=stats["input_i"],
            input_lra=stats["input_lra"],
            input_tp=stats["input_tp"],
            input_thresh=stats["input_thresh"],
            target_offset=stats["target_offset"],
        )
    except KeyError as exc:
        raise RuntimeError(f"loudnorm 输出缺少字段: {exc}") from exc


def _measure_loudness(
    sources: list[Path],
    target_lufs: float,
    true_peak: float,
    sample_rate: int,
    track_gain_db: float | None = None,
    channels: Channels = "stereo",
) -> LoudnessMeasurement:
    """第一遍：跑 loudnorm 分析拿到 measured_* 参数（分轨电平、声道转换和重采样都提前应用）。"""
    input_args: list[str] = []
    for source in sources:
        input_args.extend(["-i", str(source)])

    channel_filter = _channel_layout_filter(channels)
    resample_expr = f"aresample={sample_rate}"
    loudnorm_expr = (
        f"loudnorm=I={target_lufs}:LRA={_MEASURE_LRA}:TP={true_peak}"
        f":print_format=json"
    )

    if len(sources) == 1:
        # 声道转换与最终导出保持一致，保证测量的响度即最终输出响度
        pre_chain = ",".join(
            expr for expr in (channel_filter, resample_expr, loudnorm_expr) if expr
        )
        args = [
            "-y",
            *input_args,
            "-af",
            pre_chain,
            "-f",
            "null",
            "-",
        ]
    else:
        mix_pre = ",".join(
            expr for expr in (channel_filter, resample_expr) if expr
        )
        filter_complex = (
            f"{_build_amix_expr(len(sources), track_gain_db)}[mix];"
            f"[mix]{mix_pre}[m2];"
            f"[m2]{loudnorm_expr}[out]"
        )
        args = [
            "-y",
            *input_args,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-f",
            "null",
            "-",
        ]

    result = _invoke_ffmpeg(args, quiet=False)
    stderr_text = result.stderr.decode("utf-8", errors="replace")
    return _parse_loudness_json(stderr_text)


def _build_export_filters(
    options: AudioDownloadOptions,
    sources: list[Path],
    track_gain_db: float | None = None,
) -> list[str]:
    """构造导出用的后处理链：整条链拉到最终输出采样率，避免下游重采样引入误差。"""
    target_lufs, limit_db = _resolve_loudness_params(options)
    chain: list[str] = []

    # 声道转换置于链首（测量同样提前应用，保证响度测量与最终输出一致）；
    # 「与源相同」时保持源声道数，不添加该 filter。
    channel_filter = _channel_layout_filter(options.channels)
    if channel_filter:
        chain.append(channel_filter)

    # 把测量、增益、限幅统一拉到目标输出采样率，避免下游重采样引入 LUFS 漂移。
    if target_lufs is not None or limit_db is not None:
        chain.append(f"aresample={options.sample_rate}")

    if target_lufs is not None:
        true_peak = limit_db if limit_db is not None else -1.5
        measurement = _measure_loudness(
            sources,
            target_lufs,
            true_peak,
            options.sample_rate,
            track_gain_db,
            options.channels,
        )
        # 第二遍：拿 measured_i 手工算增益，纯线性 volume 偏移；
        # 不用 loudnorm 的 linear 模式——它自带的前瞻真峰限幅会在峰值触顶时压掉大动态段，
        # 而且 LRA/TP 条件不满足时还会静默回退成动态压缩。
        try:
            input_i = float(measurement.input_i)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"loudnorm 未返回可用的 input_i: {measurement.input_i}") from exc
        gain_db = target_lufs - input_i
        chain.append(f"volume={gain_db:.2f}dB")

    if limit_db is not None:
        # alimiter 是样本域限幅器，只保证每个采样点 ≤ limit，但相邻采样点之间的
        # 模拟重建波形（intersample peak / dBTP）会因带限重建冲高 0.3~1 dB。
        # 用 4x 过采样把 alimiter 包起来：在过采样率下的样本域限幅约等于原采样率
        # 下的真峰限幅（实测真峰从 0.0 dBFS 降到 -0.9 dBTP，接近目标 -1）。
        oversample_rate = options.sample_rate * LIMITER_OVERSAMPLE
        chain.append(f"aresample={oversample_rate}")
        chain.append(f"alimiter=limit={limit_db}dB:attack=5:release=50:level=disabled")
        chain.append(f"aresample={options.sample_rate}")

    return chain


def _export_single_source(source: Path, output_path: Path, options: AudioDownloadOptions) -> None:
    filters = _build_export_filters(options, [source])
    args = ["-y", "-i", str(source)]
    if filters:
        args.extend(["-af", ",".join(filters)])
    args.extend([*_build_ffmpeg_output_args(options), str(output_path)])
    _run_ffmpeg(args)


def _mix_and_export(sources: list[Path], output_path: Path, options: AudioDownloadOptions) -> None:
    input_args: list[str] = []
    for source in sources:
        input_args.extend(["-i", str(source)])

    track_gain_db = _resolve_track_gain_db(options)
    filters = _build_export_filters(options, sources, track_gain_db)
    segments = [f"{_build_amix_expr(len(sources), track_gain_db)}[a0]"]
    output_label = "[a0]"
    for index, post_filter in enumerate(filters):
        next_label = f"[p{index}]"
        segments.append(f"{output_label}{post_filter}{next_label}")
        output_label = next_label

    _run_ffmpeg(
        [
            "-y",
            *input_args,
            "-filter_complex",
            ";".join(segments),
            "-map",
            output_label,
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
