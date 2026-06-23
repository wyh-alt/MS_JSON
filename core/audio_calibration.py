"""根据 MR 旋律参考音频与 MIDI 音符对齐校准时间轴。"""
from __future__ import annotations

import os
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.parser import SongData
from core.audio_downloader import (
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


@dataclass(frozen=True)
class AudioCalibrationResult:
    offset_ms: int
    matched_audio_ms: int
    matched_midi_ms: int
    midi_first_note_ms: int
    match_count: int
    audio_source: str
    decode_source: str

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
        )
        os.replace(temp_path, wav_cache)
    except subprocess.CalledProcessError as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg 解码失败: {stderr or exc}") from exc

    return wav_cache
