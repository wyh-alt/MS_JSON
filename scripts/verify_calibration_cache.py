"""验证音频校准结果共享缓存（同一首歌只做一次校准、全模块共享）。

覆盖场景：
1. 成功结果缓存复用（第二次调用不再计算）
2. 失败结果缓存复用（各模块一致地视为"无校准"）
3. 失败结果 TTL 过期后允许重试恢复
4. 音频文件状态变化（下载完成/替换）后自动失效重算
5. 并发调用同歌曲：per-key 锁保证只计算一次、结果一致
6. 手动偏移不缓存，每次实时叠加

运行：python scripts/verify_calibration_cache.py
"""
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import audio_calibration as ac
from core.parser import Note, SongData


def make_song(json_dir: str, audio_path: str) -> SongData:
    return SongData(
        source_path=str(Path(json_dir) / "song.json"),
        mr_id=1,
        title="t",
        original_key="m",
        notes=[Note(start=1000, end=1500, key=60, is_part_a=True, is_part_b=False)],
        file_mr_mel_m=audio_path,
    )


def fake_compute(offset: int = 2000, fail: str | None = None):
    if fail:
        raise ValueError(fail)
    return ac.AudioCalibrationResult(
        offset_ms=offset,
        matched_audio_ms=1000,
        matched_midi_ms=1000,
        midi_first_note_ms=1000,
        match_count=3,
        audio_source="a",
        decode_source="d",
    )


def reset_cache():
    ac._CALIBRATION_CACHE.clear()
    ac._CALIBRATION_LOCKS.clear()


passed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed
    assert cond, f"[FAIL] {name} {detail}"
    passed += 1
    print(f"[PASS] {name}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        json_dir = Path(tmp)
        audio_path = json_dir / "mel.m4a"
        audio_path.write_bytes(b"fake-audio-data")
        song = make_song(tmp, str(audio_path))

        # ── 1. 成功结果缓存复用 ──────────────────────────────
        reset_cache()
        with (
            patch.object(ac, "resolve_mr_mel_url", return_value=str(audio_path)),
            patch.object(ac, "compute_audio_calibration_offset", side_effect=[fake_compute()]) as comp,
        ):
            r1 = ac.resolve_export_time_offset(song)
            r2 = ac.resolve_export_time_offset(song)
            r3 = ac.resolve_export_time_offset(song, time_offset_ms=500)
        check("成功结果缓存复用", r1[0] == 2000 and r2[0] == 2000, f"got {r1[0]},{r2[0]}")
        check("成功结果只计算一次", comp.call_count == 1, f"calls={comp.call_count}")
        check("手动偏移实时叠加", r3[0] == 2500, f"got {r3[0]}")
        check("成功结果返回诊断对象", r2[1] is not None and r2[2] is None)

        # ── 2. 失败结果缓存复用 ──────────────────────────────
        reset_cache()
        with (
            patch.object(ac, "resolve_mr_mel_url", return_value=str(audio_path)),
            patch.object(ac, "compute_audio_calibration_offset", side_effect=[ValueError("boom")]) as comp,
        ):
            r1 = ac.resolve_export_time_offset(song)
            r2 = ac.resolve_export_time_offset(song)
        check("失败结果缓存复用", r1[0] == 0 and r1[2] == "boom" and r2[0] == 0 and r2[2] == "boom",
              f"got {r1}, {r2}")
        check("失败结果只计算一次", comp.call_count == 1, f"calls={comp.call_count}")

        # ── 3. 失败 TTL 过期后重试 ───────────────────────────
        reset_cache()
        with (
            patch.object(ac, "resolve_mr_mel_url", return_value=str(audio_path)),
            patch.object(ac, "compute_audio_calibration_offset",
                         side_effect=[ValueError("boom"), fake_compute()]) as comp,
        ):
            r1 = ac.resolve_export_time_offset(song)
            # 过期缓存 → 重新计算 → 成功
            ac._CALIBRATION_CACHE[next(iter(ac._CALIBRATION_CACHE))] = (None, "boom", 0.0)
            r2 = ac.resolve_export_time_offset(song)
        check("失败 TTL 过期后重试", r1[0] == 0 and r2[0] == 2000, f"got {r1[0]},{r2[0]}")
        check("过期重试触发重新计算", comp.call_count == 2, f"calls={comp.call_count}")

        # ── 4. 音频文件状态变化后失效 ────────────────────────
        reset_cache()
        with (
            patch.object(ac, "resolve_mr_mel_url", return_value=str(audio_path)),
            patch.object(ac, "compute_audio_calibration_offset", side_effect=[fake_compute(), fake_compute(offset=3000)]) as comp,
        ):
            r1 = ac.resolve_export_time_offset(song)
            # 模拟音频被替换（mtime/size 变化）
            audio_path.write_bytes(b"new-audio-data-larger")
            r2 = ac.resolve_export_time_offset(song)
        check("音频替换后重新校准", r1[0] == 2000 and r2[0] == 3000, f"got {r1[0]},{r2[0]}")
        check("替换后触发重新计算", comp.call_count == 2, f"calls={comp.call_count}")

        # ── 5. 交付并行场景：MIDI/歌词/段落 三线程同时校准同一首歌 ──
        # 模拟交付资源一键提取：三个项目各自线程对同一 JSON 独立调用
        # resolve_export_time_offset()，per-key 锁应保证只做一次完整校准，
        # 三个项目拿到完全相同的偏移。
        reset_cache()
        with (
            patch.object(ac, "resolve_mr_mel_url", return_value=str(audio_path)),
            patch.object(ac, "compute_audio_calibration_offset", side_effect=[fake_compute()]) as comp,
        ):
            results: list[tuple] = []
            barrier = threading.Barrier(3)

            def worker():
                barrier.wait()
                results.append(ac.resolve_export_time_offset(song))

            threads = [threading.Thread(target=worker) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        check("三项目并发结果一致", all(r[0] == 2000 for r in results), f"got {results}")
        check("三项目并发只校准一次", comp.call_count == 1, f"calls={comp.call_count}")

        # ── 6. 无旋律音频：确定性失败，不缓存 ────────────────
        reset_cache()
        song_no_mel = make_song(tmp, str(audio_path))
        song_no_mel.file_mr_mel_m = ""
        with patch.object(ac, "resolve_mr_mel_url", return_value=None) as mel:
            r1 = ac.resolve_export_time_offset(song_no_mel)
            r2 = ac.resolve_export_time_offset(song_no_mel)
        check("无旋律音频一致失败", r1[0] == 0 and r1[2] == r2[2] and "file_mr_mel" in r1[2], f"got {r1}")

    print(f"\n全部 {passed} 项检查通过")


if __name__ == "__main__":
    main()
