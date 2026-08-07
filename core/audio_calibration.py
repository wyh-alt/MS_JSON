"""根据 MR 旋律参考音频与 MIDI 音符对齐校准时间轴。"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.parser import SongData
from core.audio_downloader import (
    _CREATE_NO_WINDOW,
    cached_audio_path,
    find_ffmpeg_executable,
    resolve_audio_file,
    resolve_mr_mel_url,
)

CALIBRATION_ANALYZE_MS = 30_000
HOP_LENGTH = 512
FRAME_LENGTH = 2048
ENERGY_RISE_RATIO = 0.04
ATTACK_WINDOW_FRAMES = 20
ATTACK_DURATION_THRESHOLD_MS = 300
FAST_ATTACK_RATIOS = (0.18, 0.35, 0.45)
SLOW_ATTACK_RATIOS = (0.30, 0.50, 0.65)
MIDI_MATCH_GAP_MS = 4_000
ALIGN_TOLERANCE_MS = 60
TARGET_SAMPLE_RATE = 22050

# 多模块并行（如交付资源一键提取）可能对同一音频同时校准，
# PCM 转换的 .part.wav + os.replace 按缓存路径串行化避免竞态
_PCM_WAV_LOCKS: dict[str, threading.Lock] = {}
_PCM_WAV_LOCKS_GUARD = threading.Lock()

# 同一首歌的校准结果按歌曲共享：歌词/段落/MIDI 等模块各自调用
# resolve_export_time_offset()，第一次计算后缓存，后续模块直接复用，
# 避免各模块独立计算导致"一个带校准、一个没带"的不一致。
# key = (音频稳定标识, JSON 父目录, 音频文件状态)；文件状态参与 key，
# 音频下载完成/被替换后自动失效重算。
# 成功与失败结果都缓存：校准失败对这首歌是确定性的（音频缺失、解码失败等），
# 各模块应保持一致地视为"无校准"。失败结果带 TTL（默认 5 分钟），
# 网络抖动等暂时性失败在下次运行时可重试恢复；成功结果无 TTL，仅随文件状态失效。
_CALIBRATION_CACHE: dict[
    tuple,
    tuple[AudioCalibrationResult | None, str | None, float],
] = {}
_CALIBRATION_LOCKS: dict[tuple, threading.Lock] = {}
_CALIBRATION_GUARD = threading.Lock()
_CALIBRATION_FAILURE_TTL_S = 300.0


@dataclass(frozen=True)
class AudioCalibrationResult:
    offset_ms: int
    matched_audio_ms: int
    matched_midi_ms: int
    midi_first_note_ms: int
    match_count: int
    audio_source: str
    decode_source: str


def _file_state(path: Path) -> tuple[int, int] | None:
    """文件状态 (mtime_ns, size)；文件不存在时为 None。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _audio_file_state(
    mel_url: str, json_path: str
) -> tuple[str, tuple[int, int] | None]:
    """返回 (音频稳定标识, 文件状态)；文件未就绪（本地缺失/缓存未下载）时状态为 None。

    标识与 resolve_audio_file() 的解析结果对应：URL 用其本身（缓存路径由
    cached_audio_path 计算），本地路径用实际存在的文件绝对路径。文件状态参与
    校准缓存 key，音频下载完成或被替换后自动失效重算。
    """
    value = (mel_url or "").strip()
    if value.startswith(("http://", "https://")):
        return value, _file_state(cached_audio_path(value, json_path, ".m4a"))
    path = Path(value)
    if path.is_file():
        return str(path), _file_state(path)
    by_name = Path(json_path).parent / path.name
    if by_name.is_file():
        return str(by_name), _file_state(by_name)
    return value, None


def _calibration_lock(key: tuple) -> threading.Lock:
    """同一首歌的并发校准串行化（锁随 key 常驻，条目数与歌曲数一致）。"""
    with _CALIBRATION_GUARD:
        lock = _CALIBRATION_LOCKS.setdefault(key, threading.Lock())
    return lock


def _cached_calibration(
    cache_key: tuple,
) -> tuple[AudioCalibrationResult | None, str | None] | None:
    """读取校准缓存；失败结果超过 TTL 视为未命中（允许重试恢复）。"""
    with _CALIBRATION_GUARD:
        cached = _CALIBRATION_CACHE.get(cache_key)
    if cached is None:
        return None
    calibration, error, cached_at = cached
    if calibration is None and time.monotonic() - cached_at > _CALIBRATION_FAILURE_TTL_S:
        return None
    return calibration, error


def resolve_export_time_offset(
    song: SongData,
    *,
    time_offset_ms: int = 0,
    audio_reference_calibration: bool = True,
) -> tuple[int, AudioCalibrationResult | None, str | None]:
    """计算导出用的总时间偏移（手动偏移 + 音频参考校准）。

    同一首歌的校准结果按歌曲共享缓存：歌词/段落/MIDI 等模块即使独立调用
    也得到同一偏移；成功与失败结果都缓存，音频文件状态变化后自动重算。
    """
    if not audio_reference_calibration:
        return time_offset_ms, None, None

    mel_url = resolve_mr_mel_url(song)
    if not mel_url:
        # 无旋律音频：确定性失败，不缓存，各模块一致
        key = (song.original_key or "").strip() or "?"
        return time_offset_ms, None, f"无法根据 original_key={key!r} 找到 file_mr_mel 音频"

    stable_id, file_state = _audio_file_state(mel_url, song.source_path)
    json_dir = str(Path(song.source_path).parent)
    cache_key = (stable_id, json_dir, file_state)

    cached = _cached_calibration(cache_key)
    if cached is not None:
        calibration, error = cached
        if calibration is not None:
            return time_offset_ms + calibration.offset_ms, calibration, None
        return time_offset_ms, None, error

    # 同歌曲的并发校准串行化，避免一个模块失败、另一个等锁重试成功后结果不一致
    with _calibration_lock((stable_id, json_dir)):
        # 等待锁期间音频可能已被下载/替换，按最新文件状态复查缓存
        _, file_state_now = _audio_file_state(mel_url, song.source_path)
        cache_key_now = (stable_id, json_dir, file_state_now)
        cached = _cached_calibration(cache_key_now)
        if cached is not None:
            calibration, error = cached
            if calibration is not None:
                return time_offset_ms + calibration.offset_ms, calibration, None
            return time_offset_ms, None, error

        calibration: AudioCalibrationResult | None = None
        calibration_error: str | None = None
        total_offset_ms = time_offset_ms
        try:
            calibration = compute_audio_calibration_offset(song)
            total_offset_ms += calibration.offset_ms
        except Exception as exc:
            calibration_error = str(exc)
        with _CALIBRATION_GUARD:
            _CALIBRATION_CACHE[cache_key_now] = (
                calibration,
                calibration_error,
                time.monotonic(),
            )
    return total_offset_ms, calibration, calibration_error


def format_calibration_log_message(calibration: AudioCalibrationResult) -> str:
    return (
        f"音频校准 {calibration.offset_ms:+d} ms "
        f"(匹配 MIDI {calibration.matched_midi_ms} ms ↔ "
        f"音频 {calibration.matched_audio_ms} ms, "
        f"命中 {calibration.match_count} 个音符)"
    )


def append_calibration_log(
    calibration_log: list[str] | None,
    *,
    audio_reference_calibration: bool,
    calibration: AudioCalibrationResult | None,
    calibration_error: str | None,
) -> None:
    if calibration_log is None or not audio_reference_calibration:
        return
    if calibration is not None:
        calibration_log.append(format_calibration_log_message(calibration))
    else:
        calibration_log.append(
            f"音频校准跳过（{calibration_error or '未知原因'}）"
        )


def first_note_start_ms(song: SongData) -> int | None:
    if not song.notes:
        return None
    return min(note.start for note in song.notes)


def compute_audio_calibration_offset(song: SongData) -> AudioCalibrationResult:
    """将首个可感知旋律音对齐到对应 MIDI 音符，跳过音频前的无效 MIDI。"""
    mel_url = resolve_mr_mel_url(song)
    if not mel_url:
        key = (song.original_key or "").strip() or "?"
        raise ValueError(f"无法根据 original_key={key!r} 找到 file_mr_mel 音频")

    midi_first_ms = first_note_start_ms(song)
    if midi_first_ms is None:
        raise ValueError("歌曲没有可导出的 MIDI 音符")

    audio_path = resolve_audio_file(mel_url, song.source_path)
    decode_path = _ensure_pcm_wav(audio_path)
    envelope = _analyze_attack_envelope(str(decode_path))
    audio_first_ms = envelope.perceived_note_ms
    midi_starts = sorted({note.start for note in song.notes})
    matched_midi_ms = _match_midi_note_for_audio(midi_starts, audio_first_ms)
    offset_ms = audio_first_ms - matched_midi_ms
    match_count = _count_aligned_notes(
        midi_starts,
        envelope.attack_markers_ms,
        offset_ms,
    )
    return AudioCalibrationResult(
        offset_ms=offset_ms,
        matched_audio_ms=audio_first_ms,
        matched_midi_ms=matched_midi_ms,
        midi_first_note_ms=midi_first_ms,
        match_count=match_count,
        audio_source=str(audio_path),
        decode_source=str(decode_path),
    )


@dataclass(frozen=True)
class _AttackEnvelope:
    perceived_note_ms: int
    attack_markers_ms: list[int]
    rise_ms: int
    attack_duration_ms: int


def detect_first_perceived_note_ms(audio_path: str) -> int:
    """检测首个可感知旋律音（能量包络起音 + 自适应攻击比例）。"""
    return _analyze_attack_envelope(audio_path).perceived_note_ms


def detect_onset_times_ms(audio_path: str) -> list[int]:
    """提取音频前段包络标记时间（毫秒）。"""
    return _analyze_attack_envelope(audio_path).attack_markers_ms


def _analyze_attack_envelope(audio_path: str) -> _AttackEnvelope:
    y, sr = _load_mono_audio_segment(audio_path, CALIBRATION_ANALYZE_MS)
    if y.size == 0:
        raise ValueError(f"音频为空: {audio_path}")

    try:
        import librosa
    except ImportError as exc:
        raise RuntimeError("音频校准需要安装 librosa，请执行: pip install librosa") from exc

    rms = librosa.feature.rms(
        y=y,
        frame_length=FRAME_LENGTH,
        hop_length=HOP_LENGTH,
    )[0]
    times = librosa.frames_to_time(
        np.arange(len(rms)),
        sr=sr,
        hop_length=HOP_LENGTH,
    ) * 1000

    rise_threshold = float(rms.max()) * ENERGY_RISE_RATIO
    rise_indices = np.flatnonzero(rms >= rise_threshold)
    if rise_indices.size == 0:
        raise ValueError(f"未在音频中检测到能量起音: {audio_path}")

    rise_idx = int(rise_indices[0])
    rise_ms = int(round(times[rise_idx]))
    attack_end_idx = min(len(rms), rise_idx + ATTACK_WINDOW_FRAMES)
    attack_segment = rms[rise_idx:attack_end_idx]
    if attack_segment.size == 0:
        raise ValueError(f"音频起音窗口无效: {audio_path}")

    peak_rel = int(np.argmax(attack_segment))
    attack_duration_ms = int(round(times[rise_idx + peak_rel] - times[rise_idx]))
    ratios = (
        FAST_ATTACK_RATIOS
        if attack_duration_ms < ATTACK_DURATION_THRESHOLD_MS
        else SLOW_ATTACK_RATIOS
    )

    marker_times = [
        _attack_ratio_time_ms(times, rms, rise_idx, attack_end_idx, ratio)
        for ratio in ratios
    ]
    perceived_note_ms = sum(marker_times) // len(marker_times)
    return _AttackEnvelope(
        perceived_note_ms=perceived_note_ms,
        attack_markers_ms=marker_times,
        rise_ms=rise_ms,
        attack_duration_ms=attack_duration_ms,
    )


def _attack_ratio_time_ms(
    times: np.ndarray,
    rms: np.ndarray,
    rise_idx: int,
    attack_end_idx: int,
    ratio: float,
) -> int:
    segment = rms[rise_idx:attack_end_idx]
    floor_value = float(segment[0])
    peak_value = float(segment.max())
    if peak_value <= floor_value:
        return int(round(times[rise_idx]))

    target = floor_value + ratio * (peak_value - floor_value)
    for offset, value in enumerate(segment):
        if value >= target:
            return int(round(times[rise_idx + offset]))
    return int(round(times[attack_end_idx - 1]))


def _match_midi_note_for_audio(
    midi_starts: list[int],
    audio_first_ms: int,
) -> int:
    candidates = [
        midi_ms
        for midi_ms in midi_starts
        if abs(midi_ms - audio_first_ms) <= MIDI_MATCH_GAP_MS
    ]
    if not candidates:
        raise ValueError(
            f"音频首音 {audio_first_ms} ms 附近未找到可匹配的 MIDI 音符"
        )
    return min(candidates, key=lambda midi_ms: abs(midi_ms - audio_first_ms))


def _count_aligned_notes(
    midi_starts: list[int],
    audio_markers: list[int],
    offset_ms: int,
) -> int:
    if not audio_markers:
        return 1
    return sum(
        1
        for midi_ms in midi_starts[:180]
        if any(
            abs(midi_ms + offset_ms - audio_ms) <= ALIGN_TOLERANCE_MS
            for audio_ms in audio_markers
        )
    )


def _load_mono_audio_segment(audio_path: str, max_ms: int) -> tuple[np.ndarray, int]:
    y, sr = _load_mono_audio(audio_path)
    max_samples = int(sr * max_ms / 1000)
    return y[:max_samples], sr


def _load_mono_audio(audio_path: str) -> tuple[np.ndarray, int]:
    path = Path(audio_path)
    if path.suffix.lower() == ".wav":
        return _read_wav_mono(path)
    try:
        import librosa

        y, sr = librosa.load(str(path), sr=TARGET_SAMPLE_RATE, mono=True)
        return y, sr
    except ImportError as exc:
        raise RuntimeError("音频校准需要安装 librosa，请执行: pip install librosa") from exc


def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise ValueError(f"不支持的 WAV 位深: {path}")

    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, sample_rate


def _ensure_pcm_wav(audio_path: Path) -> Path:
    """将 m4a 等格式用 ffmpeg 解码为 PCM WAV，减少 AAC 解码时间偏差。"""
    if audio_path.suffix.lower() == ".wav":
        return audio_path

    wav_cache = audio_path.with_suffix(audio_path.suffix + ".pcm.wav")
    if wav_cache.is_file() and wav_cache.stat().st_size > 0:
        if wav_cache.stat().st_mtime >= audio_path.stat().st_mtime:
            return wav_cache

    with _PCM_WAV_LOCKS_GUARD:
        lock = _PCM_WAV_LOCKS.setdefault(str(wav_cache), threading.Lock())
    with lock:
        # 等待锁期间其他线程可能已完成转换，二次检查
        if wav_cache.is_file() and wav_cache.stat().st_size > 0:
            if wav_cache.stat().st_mtime >= audio_path.stat().st_mtime:
                return wav_cache

        ffmpeg = find_ffmpeg_executable()
        if ffmpeg is None:
            return audio_path

        temp_path = wav_cache.with_suffix(".part.wav")
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(audio_path),
                    "-ac",
                    "1",
                    "-ar",
                    str(TARGET_SAMPLE_RATE),
                    "-c:a",
                    "pcm_s16le",
                    str(temp_path),
                ],
                check=True,
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
            os.replace(temp_path, wav_cache)
        except subprocess.CalledProcessError as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            stderr = exc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg 解码失败: {stderr or exc}") from exc

    return wav_cache
