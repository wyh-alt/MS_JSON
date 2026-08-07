"""验证交付模块按歌曲生命周期工作流的行为。

覆盖场景：
1. 每首歌内部按 伴奏→MIDI→歌词→段落 严格顺序执行
2. 歌曲之间并行（并发数内多首歌同时处理）
3. 单曲单项目失败隔离：不影响该曲其他项目与其他曲目
4. 段落行逐曲收集后统一写 Excel（一次写入，行数正确）
5. 结果结构兼容（projects 按展示顺序，成功/失败/说明聚合正确）

运行：python scripts/verify_delivery_workflow.py
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import audio_calibration as ac
from core.delivery_exporter import (
    PROJECT_AUDIO,
    PROJECT_LYRIC,
    PROJECT_MIDI,
    PROJECT_SECTIONS,
    run_delivery_export,
)
from core.midi_exporter import _prepare_song_for_export
from core.parser import Note, SongData, SongSection

passed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed
    assert cond, f"[FAIL] {name} {detail}"
    passed += 1
    print(f"[PASS] {name}")


def make_song(mr_id: int) -> SongData:
    return SongData(
        source_path=f"x/{mr_id}.json",
        mr_id=mr_id,
        title=f"t{mr_id}",
        original_key="m",
        notes=[Note(start=0, end=100, key=60, is_part_a=True, is_part_b=False)],
    )


def main():
    json_paths = [str(Path("x") / f"{mr_id}.json") for mr_id in (1, 2, 3)]
    call_log: list[tuple[str, int]] = []
    log_lock = threading.Lock()
    concurrency = {"active": 0, "peak": 0}  # 同时活跃的歌曲 worker 数
    active_lock = threading.Lock()

    def fake_load(path, lyric_field="ori"):
        return make_song(int(Path(path).stem))

    def track(name: str, song: SongData, delay: float = 0.0):
        with log_lock:
            call_log.append((name, song.mr_id))
        with active_lock:
            concurrency["active"] += 1
            concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
        try:
            if delay:
                time.sleep(delay)
        finally:
            with active_lock:
                concurrency["active"] -= 1

    def fake_audio_delayed(song, outdir):
        track("audio", song, delay=0.15)
        return f"{outdir}/audio-{song.mr_id}.wav"

    def fake_midi(song, outdir):
        track("midi", song)
        return [f"{outdir}/midi-{song.mr_id}.mid"], [f"音频校准 +{song.mr_id * 100} ms"]

    def fake_lyric(song, outdir):
        track("lyric", song)
        return f"{outdir}/lyric-{song.mr_id}.txt", []

    def fake_sections(song):
        track("sections", song)
        return [(str(song.mr_id), "t", "a", "前奏", "00:00.000", "00:01.000", "hi")]

    fake_excel = "fake/excel.xlsx"

    def fake_write(rows, outdir):
        check("段落 Excel 一次性写入全部歌曲行", len(rows) == 3, f"rows={len(rows)}")
        return fake_excel

    # ── 1+2. 顺序 + 并发 ──────────────────────────────────────
    call_log.clear()
    with (
        patch("core.delivery_exporter.load_song_json", side_effect=fake_load),
        patch("core.delivery_exporter._run_audio_for_song", side_effect=fake_audio_delayed),
        patch("core.delivery_exporter._run_midi_for_song", side_effect=fake_midi),
        patch("core.delivery_exporter._run_lyric_for_song", side_effect=fake_lyric),
        patch("core.delivery_exporter._run_sections_for_song", side_effect=fake_sections),
        patch("core.delivery_exporter.write_sections_excel", side_effect=fake_write),
    ):
        result = run_delivery_export(
            json_paths, "out", enabled={PROJECT_AUDIO, PROJECT_MIDI, PROJECT_LYRIC, PROJECT_SECTIONS},
            max_workers=2,
        )
    for mr_id in (1, 2, 3):
        seq = [name for name, mid in call_log if mid == mr_id]
        check(
            f"歌曲 {mr_id} 内部顺序",
            seq == ["audio", "midi", "lyric", "sections"],
            f"got {seq}",
        )
    check("歌曲级并发生效（峰值活跃数 >= 2）", concurrency["peak"] >= 2, f"peak={concurrency['peak']}")

    # ── 结果结构（展示顺序 + 聚合）───────────────────────────
    names = [p.name for p in result.projects]
    check("结果按展示顺序", names == ["伴奏处理", "MIDI处理", "歌词处理", "段落信息导出"], f"got {names}")
    check("伴奏成功 3 首", len(result.projects[0].success) == 3)
    check("MIDI 成功 3 首且含校准说明", len(result.projects[1].success) == 3
          and "音频校准" in result.projects[1].notes[0])
    check("歌词成功 3 首", len(result.projects[2].success) == 3)
    check("段落 Excel 成功 1 个表格", result.projects[3].success == [fake_excel])
    check("段落说明含总段数", any("共 3 段" in n for n in result.projects[3].notes))
    check("无失败", all(not p.failed for p in result.projects))

    # ── 3. 失败隔离：歌曲 2 的伴奏失败 ────────────────────────
    call_log.clear()

    def fake_audio_fail(song, outdir):
        track("audio", song)
        if song.mr_id == 2:
            raise RuntimeError("模拟伴奏失败")
        return f"{outdir}/audio-{song.mr_id}.wav"

    with (
        patch("core.delivery_exporter.load_song_json", side_effect=fake_load),
        patch("core.delivery_exporter._run_audio_for_song", side_effect=fake_audio_fail),
        patch("core.delivery_exporter._run_midi_for_song", side_effect=fake_midi),
        patch("core.delivery_exporter._run_lyric_for_song", side_effect=fake_lyric),
        patch("core.delivery_exporter._run_sections_for_song", side_effect=fake_sections),
        patch("core.delivery_exporter.write_sections_excel", side_effect=fake_write),
    ):
        result = run_delivery_export(
            json_paths, "out", enabled={PROJECT_AUDIO, PROJECT_MIDI, PROJECT_LYRIC, PROJECT_SECTIONS},
            max_workers=2,
        )
    audio_p, midi_p = result.projects[0], result.projects[1]
    check("伴奏项目失败 1 首", len(audio_p.failed) == 1 and audio_p.failed[0][1] == "模拟伴奏失败",
          f"got {audio_p.failed}")
    check("歌曲 2 其他项目仍执行", [name for name, mid in call_log if mid == 2]
          == ["audio", "midi", "lyric", "sections"])
    check("MIDI 仍成功 3 首", len(midi_p.success) == 3)

    # ── 4. 整首 rap：所有模块一致跳过校准，MIDI 不产出 ──────
    # 整首 rap（过滤 rap + 非旋律后无音符）没有旋律可对齐：
    # 校准判定直接跳过（无校准），MIDI 导出随后因无音符失败。
    rap_song = SongData(
        source_path="x/rap.json",
        mr_id=9,
        title="rap",
        original_key="m",
        notes=[Note(start=100, end=500, key=60, is_part_a=True, is_part_b=False)],
        sections=[SongSection(name="rap", start=0, end=1000, seq=0)],
    )
    check("整首 rap：无有效旋律判定", not ac._has_exportable_melody(rap_song))
    check("普通歌曲：有效旋律判定通过", ac._has_exportable_melody(make_song(1)))

    # 真实链路：整首 rap 的校准判定直接跳过（不尝试匹配），失败结果进缓存，
    # 歌词/段落随后调用拿到一致的无校准结果。
    ac._CALIBRATION_CACHE.clear()
    ac._CALIBRATION_LOCKS.clear()
    with patch("core.audio_calibration.resolve_mr_mel_url", return_value="x/rap.mel.m4a"):
        r = ac.resolve_export_time_offset(rap_song)  # MIDI/歌词/段落共用
        r2 = ac.resolve_export_time_offset(rap_song)  # 共享缓存
    check("整首 rap：校准跳过(无偏移)", r[0] == 0, f"got {r}")
    check("整首 rap：原因明确", r[2] is not None and "整首rap" in r[2], f"got {r[2]}")
    check("整首 rap：缓存共享一致", r2[0] == 0 and r2[2] == r[2])

    # MIDI 内部：校准先于过滤执行（顺序不变），过滤后导出音符为空
    received_note_count: dict[str, int] = {}

    def fake_resolve(song, *, time_offset_ms=0, audio_reference_calibration=True):
        received_note_count["count"] = len(song.notes)
        return 2000, None, None

    with patch("core.audio_calibration.resolve_export_time_offset", side_effect=fake_resolve):
        out_song, _, _ = _prepare_song_for_export(
            rap_song, "merge_same",
            exclude_rap_sections=True, remove_non_melody_notes=True,
            time_offset_ms=0, audio_reference_calibration=True,
        )
    check("整首 rap：校准先于过滤执行", received_note_count["count"] == 1,
          f"got {received_note_count}")
    check("整首 rap：过滤后导出音符为空", len(out_song.notes) == 0)

    # ── 5. MIDI 失败（如整首 rap 无输出）：歌词/段落照常 ───────
    call_log.clear()

    def fake_midi_fail(song, outdir):
        track("midi", song)
        if song.mr_id == 2:
            raise ValueError("没有可导出的语种类别")
        return [f"{outdir}/midi-{song.mr_id}.mid"], []

    with (
        patch("core.delivery_exporter.load_song_json", side_effect=fake_load),
        patch("core.delivery_exporter._run_audio_for_song", side_effect=fake_audio_delayed),
        patch("core.delivery_exporter._run_midi_for_song", side_effect=fake_midi_fail),
        patch("core.delivery_exporter._run_lyric_for_song", side_effect=fake_lyric),
        patch("core.delivery_exporter._run_sections_for_song", side_effect=fake_sections),
        patch("core.delivery_exporter.write_sections_excel", side_effect=fake_write),
    ):
        result = run_delivery_export(
            json_paths, "out", enabled={PROJECT_AUDIO, PROJECT_MIDI, PROJECT_LYRIC, PROJECT_SECTIONS},
            max_workers=2,
        )
    midi_p, lyric_p, sections_p = result.projects[1], result.projects[2], result.projects[3]
    check("MIDI 失败 1 首（整首 rap 无输出）", len(midi_p.failed) == 1
          and midi_p.failed[0][1] == "没有可导出的语种类别", f"got {midi_p.failed}")
    check("MIDI 失败后歌词仍成功 3 首", len(lyric_p.success) == 3)
    check("MIDI 失败后段落仍成功", sections_p.success == [fake_excel])
    check("歌曲 2 的歌词/段落仍执行",
          [name for name, mid in call_log if mid == 2] == ["audio", "midi", "lyric", "sections"])

    print(f"\n全部 {passed} 项检查通过")


if __name__ == "__main__":
    main()
