"""校验导出的 MIDI 音符/段落 marker 绝对时间是否与 JSON 一致。"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mido

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.midi_exporter import (  # noqa: E402
    _abs_ticks,
    _export_ticks_to_ms,
    export_song,
    filter_notes,
    _dedupe_notes,
)
from core.parser import collect_json_files, load_song_json, apply_song_time_offset

MS_TOLERANCE_MS = 2  # tick 四舍五入允许误差


@dataclass
class TrackEvent:
    tick: int
    kind: str
    value: str | int


def _ticks_to_ms(tick: int, song, write_tempo: bool) -> int:
    return _export_ticks_to_ms(tick, song, write_tempo=write_tempo)


def _read_track_events(track: mido.MidiTrack) -> list[TrackEvent]:
    events: list[TrackEvent] = []
    tick = 0
    for msg in track:
        tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            events.append(TrackEvent(tick, "note_on", msg.note))
        elif msg.type == "marker":
            events.append(TrackEvent(tick, "marker", msg.text))
    return events


def _find_track_index_by_name(midi: mido.MidiFile, name: str) -> int | None:
    for index, track in enumerate(midi.tracks):
        track_name = next((msg.name for msg in track if msg.type == "track_name"), "")
        if track_name == name:
            return index
    return None


def _first_melody_track_index(midi: mido.MidiFile) -> int:
    """Type 1 导出：Track 0 为指挥轨，旋律从 Track 1 开始。"""
    return 1 if len(midi.tracks) > 1 else 0


def _expected_notes(song, part_mode: str):
    if part_mode == "merge_same":
        notes_a = filter_notes(song.notes, "A")
        notes_b = filter_notes(song.notes, "B")
        notes_o = filter_notes(song.notes, "O")
        return _dedupe_notes(notes_a + notes_b + notes_o)
    if part_mode == "merge_multi":
        return None  # 分轨单独比
    if part_mode == "separate":
        return None
    if part_mode == "male_only":
        return filter_notes(song.notes, "A")
    if part_mode == "female_only":
        return filter_notes(song.notes, "B")
    raise ValueError(part_mode)


def verify_export(
    json_path: str,
    *,
    part_mode: str = "merge_same",
    write_tempo: bool = False,
    write_section_markers: bool = True,
    time_offset_ms: int = 0,
    audio_reference_calibration: bool = False,
) -> list[str]:
    issues: list[str] = []
    song = apply_song_time_offset(load_song_json(json_path), time_offset_ms)

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = export_song(
            load_song_json(json_path),
            tmpdir,
            part_mode,
            write_tempo=write_tempo,
            write_lyrics=False,
            write_section_markers=write_section_markers,
            time_offset_ms=time_offset_ms,
            audio_reference_calibration=audio_reference_calibration,
        )

        for midi_path in paths:
            midi = mido.MidiFile(midi_path)
            suffix = Path(midi_path).stem.split("-")[-1]

            if part_mode == "merge_multi":
                part_by_track: list[tuple[int, str, list]] = []
                for track_index, track in enumerate(midi.tracks):
                    name = next(
                        (m.name for m in track if m.type == "track_name"),
                        "",
                    )
                    if name == "A声部":
                        part_by_track.append(
                            (track_index, "A声部", filter_notes(song.notes, "A"))
                        )
                    elif name == "B声部":
                        part_by_track.append(
                            (track_index, "B声部", filter_notes(song.notes, "B"))
                        )
                    elif name == "Other":
                        part_by_track.append(
                            (track_index, "Other", filter_notes(song.notes, "O"))
                        )

                for track_index, track_suffix, expected in part_by_track:
                    events = _read_track_events(midi.tracks[track_index])
                    first_melody = _first_melody_track_index(midi)
                    markers_on_track = (
                        write_section_markers and track_index == first_melody
                    )
                    _check_events(
                        issues,
                        json_path,
                        track_suffix,
                        events,
                        expected,
                        song.sections if markers_on_track else None,
                        song,
                        write_tempo,
                    )
                continue

            if part_mode == "separate":
                if suffix == "PartA":
                    expected = filter_notes(song.notes, "A")
                elif suffix == "PartB":
                    expected = filter_notes(song.notes, "B")
                elif suffix == "Other":
                    expected = filter_notes(song.notes, "O")
                else:
                    continue
                track_index = _first_melody_track_index(midi)
                events = _read_track_events(midi.tracks[track_index])
                _check_events(
                    issues,
                    json_path,
                    suffix,
                    events,
                    expected,
                    song.sections if write_section_markers else None,
                    song,
                    write_tempo,
                )
                continue

            expected = _expected_notes(song, part_mode)
            track_index = _first_melody_track_index(midi)
            events = _read_track_events(midi.tracks[track_index])
            _check_events(
                issues,
                json_path,
                suffix,
                events,
                expected,
                song.sections if write_section_markers else None,
                song,
                write_tempo,
            )

    return issues


def _check_events(
    issues: list[str],
    json_path: str,
    suffix: str,
    events: list[TrackEvent],
    expected_notes,
    expected_sections,
    song,
    write_tempo: bool,
) -> None:
    if expected_sections is not None:
        markers = [e for e in events if e.kind == "marker"]
        if len(markers) != len(expected_sections):
            issues.append(
                f"{Path(json_path).name} [{suffix}] marker 数量 "
                f"{len(markers)} != JSON {len(expected_sections)}"
            )
        for section, marker in zip(expected_sections, markers):
            expected_tick = _abs_ticks(section.start, song, write_tempo)
            if marker.tick != expected_tick:
                got_ms = _ticks_to_ms(marker.tick, song, write_tempo)
                exp_ms = section.start
                if abs(got_ms - exp_ms) > MS_TOLERANCE_MS:
                    issues.append(
                        f"{Path(json_path).name} [{suffix}] marker "
                        f"{section.name}: tick {marker.tick} "
                        f"(={got_ms}ms) != 期望 tick {expected_tick} "
                        f"(={exp_ms}ms)"
                    )

    note_events = [e for e in events if e.kind == "note_on"]
    expected_sorted = sorted(expected_notes, key=lambda n: (n.start, n.end, n.key))
    if len(note_events) != len(expected_sorted):
        issues.append(
            f"{Path(json_path).name} [{suffix}] 音符数 "
            f"{len(note_events)} != JSON {len(expected_sorted)}"
        )
        return

    for note, event in zip(expected_sorted, note_events):
        expected_tick = _abs_ticks(note.start, song, write_tempo)
        if event.tick != expected_tick:
            got_ms = _ticks_to_ms(event.tick, song, write_tempo)
            exp_ms = note.start
            if abs(got_ms - exp_ms) > MS_TOLERANCE_MS:
                issues.append(
                    f"{Path(json_path).name} [{suffix}] 音符 "
                    f"start={note.start}ms key={note.key}: "
                    f"tick {event.tick} (={got_ms}ms) != "
                    f"期望 tick {expected_tick} (={exp_ms}ms)"
                )


def main() -> int:
    json_dir = ROOT / "MS JSON"
    paths = collect_json_files(str(json_dir), valid_only=True) if json_dir.exists() else []
    if (ROOT / "36.json").exists():
        paths.append(str(ROOT / "36.json"))
    paths = sorted(set(paths))

    configs = [
        ("merge_same", False, True, 0),
        ("merge_same", True, True, 0),
        ("merge_multi", False, True, 0),
        ("merge_multi", True, True, 0),
        ("separate", False, True, 0),
    ]

    all_issues: list[str] = []
    for path in paths:
        for part_mode, write_tempo, markers, offset in configs:
            all_issues.extend(
                verify_export(
                    path,
                    part_mode=part_mode,
                    write_tempo=write_tempo,
                    write_section_markers=markers,
                    time_offset_ms=offset,
                )
            )

    print(f"检查 JSON 文件: {len(paths)}")
    print(f"检查配置: {len(configs)} 种导出组合")
    if all_issues:
        print(f"\n发现 {len(all_issues)} 处时间不一致:\n")
        for item in all_issues[:30]:
            print(" -", item)
        if len(all_issues) > 30:
            print(f" ... 另有 {len(all_issues) - 30} 处")
        return 1

    print("\n全部通过：音符与段落 marker 绝对时间与 JSON 一致（±2ms）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
