import csv
import io
import os
import re
from dataclasses import dataclass
from typing import Literal

from core.parser import (
    LyricLine,
    LyricWord,
    SongData,
    _contains_cjk,
    apply_song_time_offset,
    format_section_display_name,
    load_song_json,
    resolve_artist,
    resolve_title,
    shift_time_ms,
)

LyricFormat = Literal["ksc-txt", "ksc", "txt", "lrc", "csv"]
LyricPart = Literal["A", "B", "all"]

LYRIC_FORMAT_LABELS: list[tuple[str, LyricFormat, str]] = [
    ("KSC小灰熊 (.txt)", "ksc-txt", ".txt"),
    ("KSC (.ksc)", "ksc", ".ksc"),
    ("TXT 分句", "txt", ".txt"),
    ("LRC", "lrc", ".lrc"),
    ("CSV", "csv", ".csv"),
]

LYRIC_PART_LABELS: list[tuple[str, LyricPart]] = [
    ("全部声部", "all"),
    ("A 声部", "A"),
    ("B 声部", "B"),
]

META_LANG_LABELS: list[tuple[str, str]] = [
    ("原文", "origin"),
    ("韩文", "ko"),
    ("英文", "en"),
]


@dataclass
class KscOptions:
    char_bracket: bool = True
    word_bracket: bool = True


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name.strip() or "unknown"


def _collect_lines(song: SongData, part: LyricPart) -> list[LyricLine]:
    if part == "A":
        return list(song.lines_part_a)
    if part == "B":
        return list(song.lines_part_b)
    merged = list(song.lines_part_a) + list(song.lines_part_b)
    merged.sort(key=lambda line: (line.start, line.end))
    return _dedupe_lyric_lines(merged)


def _dedupe_lyric_lines(lines: list[LyricLine]) -> list[LyricLine]:
    """合并全部声部时，合唱等同段落在 A/B 各有一份，按时间轴与文本去重。"""
    seen: set[tuple[int, int, str]] = set()
    unique: list[LyricLine] = []
    for line in lines:
        key = (line.start, line.end, line.text.strip())
        if key in seen:
            continue
        seen.add(key)
        unique.append(line)
    return unique


def _format_ksc_time(ms: int) -> str:
    total_seconds = ms / 1000.0
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:06.3f}"


def _format_lrc_time(ms: int) -> str:
    total_seconds = ms / 1000.0
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:05.2f}"


def _escape_ksc_text(text: str) -> str:
    return text.replace("'", "''")


def _timed_units(line: LyricLine) -> list[LyricWord]:
    """一字或一词对应一条时间信息（与 867395-MID_lyrics.txt 一致）。"""
    return [unit for unit in line.units if unit.text.strip()]


def _ksc_duration_values(line: LyricLine) -> list[int]:
    """KSC 时长：非末条用下一词/字 onset 间隔，末条延续到句尾。"""
    units = _timed_units(line)
    if not units:
        return []

    durations: list[int] = []
    for index, unit in enumerate(units):
        if index < len(units) - 1:
            durations.append(max(1, units[index + 1].start - unit.start))
        else:
            durations.append(max(1, line.end - unit.start))
    return durations


def _line_durations(line: LyricLine) -> str:
    return ",".join(str(value) for value in _ksc_duration_values(line))


def _format_ksc_display_text(
    units: list[LyricWord],
    *,
    char_bracket: bool,
    word_bracket: bool,
) -> str:
    latin_indices = [
        index
        for index, unit in enumerate(units)
        if not _contains_cjk(unit.text.strip())
    ]
    last_latin_index = latin_indices[-1] if latin_indices else -1

    parts: list[str] = []
    for index, unit in enumerate(units):
        text = unit.text.strip()
        if not text:
            continue
        if _contains_cjk(text):
            parts.append(f"[{text}]" if char_bracket else text)
            continue

        if word_bracket:
            if index == last_latin_index:
                parts.append(f"[{text}]")
            else:
                parts.append(f"[{text} ]")
        else:
            if parts:
                parts.append(" ")
            parts.append(text)
    return "".join(parts)


def _render_ksc_script(
    song: SongData,
    lines: list[LyricLine],
    *,
    title_lang: str,
    artist_lang: str,
    ksc_options: KscOptions,
) -> str:
    title = _escape_ksc_text(resolve_title(song, title_lang))
    artist = _escape_ksc_text(resolve_artist(song, artist_lang) or " ")
    rows: list[str] = [
        "karaoke := CreateKaraokeObject;",
        "karaoke.rows := 2;",
        "karaoke.clear;",
        "karaoke.font('Times New Roman');",
        "",
        f"karaoke.songname := '{title}';  // 请替换歌曲名称",
        f"karaoke.singer := '{artist}';      // 请替换歌手姓名",
        "",
    ]
    for line in lines:
        units = _timed_units(line)
        if not units:
            continue
        display_text = _format_ksc_display_text(
            units,
            char_bracket=ksc_options.char_bracket,
            word_bracket=ksc_options.word_bracket,
        )
        if not display_text.strip():
            continue
        rows.append(
            "karaoke.add("
            f"'{_format_ksc_time(line.start)}', "
            f"'{_format_ksc_time(line.end)}', "
            f"'{_escape_ksc_text(display_text)}', "
            f"'{_line_durations(line)}');"
        )
    return "\n".join(rows) + "\n"


def _render_lrc(song: SongData, lines: list[LyricLine], *, title_lang: str) -> str:
    rows = [
        f"[ti:{resolve_title(song, title_lang)}]",
        f"[ar:{resolve_artist(song, title_lang)}]",
        "",
    ]
    for line in lines:
        if line.text.strip():
            rows.append(f"[{_format_lrc_time(line.start)}]{line.text.strip()}")
    return "\n".join(rows) + "\n"


def _render_txt(song: SongData, lines: list[LyricLine], *, title_lang: str, artist_lang: str) -> str:
    rows = [
        f"歌名: {resolve_title(song, title_lang)}",
        f"歌手: {resolve_artist(song, artist_lang)}",
        "",
    ]
    for line in lines:
        rows.append(
            f"{_format_ksc_time(line.start)} - {_format_ksc_time(line.end)}  {line.text.strip()}"
        )
    return "\n".join(rows) + "\n"


def _render_csv(lines: list[LyricLine]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["start_ms", "end_ms", "text", "durations"])
    for line in lines:
        if not line.text.strip():
            continue
        writer.writerow([line.start, line.end, line.text.strip(), _line_durations(line)])
    return buffer.getvalue()


def render_lyrics(
    song: SongData,
    *,
    lyric_format: LyricFormat,
    part: LyricPart,
    title_lang: str,
    artist_lang: str,
    ksc_options: KscOptions | None = None,
) -> str:
    lines = _collect_lines(song, part)
    if not lines:
        raise ValueError("没有可导出的歌词")

    options = ksc_options or KscOptions()

    if lyric_format in ("ksc-txt", "ksc"):
        return _render_ksc_script(
            song,
            lines,
            title_lang=title_lang,
            artist_lang=artist_lang,
            ksc_options=options,
        )

    if lyric_format == "lrc":
        return _render_lrc(song, lines, title_lang=title_lang)

    if lyric_format == "txt":
        return _render_txt(
            song, lines, title_lang=title_lang, artist_lang=artist_lang
        )

    if lyric_format == "csv":
        return _render_csv(lines)

    raise ValueError(f"未知歌词格式: {lyric_format}")


def _output_extension(lyric_format: LyricFormat) -> str:
    for _label, fmt, ext in LYRIC_FORMAT_LABELS:
        if fmt == lyric_format:
            return ext
    return ".txt"


def _output_basename(song: SongData, lyric_format: LyricFormat, title_lang: str) -> str:
    title = _sanitize_filename(resolve_title(song, title_lang))
    if lyric_format == "txt":
        return f"{title}_{song.mr_id}-分句"
    return f"{title}_{song.mr_id}-歌词"


def _prepare_lyric_song(
    song: SongData,
    *,
    time_offset_ms: int = 0,
    audio_reference_calibration: bool = True,
) -> tuple[SongData, "AudioCalibrationResult | None", str | None]:
    from core.audio_calibration import AudioCalibrationResult, resolve_export_time_offset

    total_offset_ms, calibration, calibration_error = resolve_export_time_offset(
        song,
        time_offset_ms=time_offset_ms,
        audio_reference_calibration=audio_reference_calibration,
    )
    return apply_song_time_offset(song, total_offset_ms), calibration, calibration_error


def export_song_lyrics(
    song: SongData,
    output_dir: str,
    *,
    lyric_format: LyricFormat,
    part: LyricPart,
    title_lang: str,
    artist_lang: str,
    ksc_options: KscOptions | None = None,
    time_offset_ms: int = 0,
    audio_reference_calibration: bool = True,
    calibration_log: list[str] | None = None,
) -> str:
    song, calibration, calibration_error = _prepare_lyric_song(
        song,
        time_offset_ms=time_offset_ms,
        audio_reference_calibration=audio_reference_calibration,
    )
    if calibration_log is not None:
        from core.audio_calibration import append_calibration_log

        append_calibration_log(
            calibration_log,
            audio_reference_calibration=audio_reference_calibration,
            calibration=calibration,
            calibration_error=calibration_error,
        )
    content = render_lyrics(
        song,
        lyric_format=lyric_format,
        part=part,
        title_lang=title_lang,
        artist_lang=artist_lang,
        ksc_options=ksc_options,
    )
    filename = f"{_output_basename(song, lyric_format, title_lang)}{_output_extension(lyric_format)}"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path


SECTION_EXPORT_HEADERS = (
    "伴奏ID",
    "歌名",
    "歌手",
    "段落类型",
    "起始时间",
    "结束时间",
    "首句歌词",
    "末句歌词",
)


def _format_section_time(ms: int) -> str:
    return _format_ksc_time(ms)


def _section_export_times(
    section_start_ms: int,
    section_end_ms: int,
    offset_ms: int,
) -> tuple[int, int]:
    """段落表格时间：原起始为 0 的段落（如 intro）保持从 0 开始，其余按偏移校准。"""
    if section_start_ms == 0:
        start_ms = 0
    else:
        start_ms = shift_time_ms(section_start_ms, offset_ms)
    end_ms = shift_time_ms(section_end_ms, offset_ms)
    if end_ms <= start_ms:
        end_ms = start_ms + 1
    return start_ms, end_ms


def collect_section_export_rows(
    song: SongData,
    *,
    title_lang: str,
    artist_lang: str,
    time_offset_ms: int = 0,
    audio_reference_calibration: bool = True,
) -> list[tuple[str, str, str, str, str, str, str, str]]:
    from core.audio_calibration import resolve_export_time_offset

    total_offset_ms, _, _ = resolve_export_time_offset(
        song,
        time_offset_ms=time_offset_ms,
        audio_reference_calibration=audio_reference_calibration,
    )
    title = resolve_title(song, title_lang)
    artist = resolve_artist(song, artist_lang) or ""
    rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    for info in song.section_export_infos:
        start_ms, end_ms = _section_export_times(
            info.section_start_ms,
            info.section_end_ms,
            total_offset_ms,
        )
        rows.append(
            (
                str(song.mr_id),
                title,
                artist,
                format_section_display_name(info.name),
                _format_section_time(start_ms),
                _format_section_time(end_ms),
                info.first_line_text,
                info.last_line_text,
            )
        )
    return rows


def write_sections_excel(
    rows: list[tuple[str, str, str, str, str, str, str, str]],
    output_dir: str,
) -> str:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:
        raise RuntimeError("缺少 openpyxl 依赖，请先安装：pip install openpyxl") from exc

    if not rows:
        raise ValueError("没有可导出的段落信息")

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "段落信息.xlsx")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "段落信息"
    sheet.append(list(SECTION_EXPORT_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        sheet.append(list(row))

    for column_cells in sheet.columns:
        max_length = 0
        column_letter = column_cells[0].column_letter
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(value))
        sheet.column_dimensions[column_letter].width = min(max(max_length + 2, 10), 48)

    workbook.save(output_path)
    return output_path


def export_sections_excel(
    json_paths: list[str],
    output_dir: str,
    *,
    lyric_field: str,
    title_lang: str,
    artist_lang: str,
    time_offset_ms: int = 0,
) -> str:
    all_rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    for path in json_paths:
        song = load_song_json(path, lyric_field)
        all_rows.extend(
            collect_section_export_rows(
                song,
                title_lang=title_lang,
                artist_lang=artist_lang,
                time_offset_ms=time_offset_ms,
            )
        )
    return write_sections_excel(all_rows, output_dir)
