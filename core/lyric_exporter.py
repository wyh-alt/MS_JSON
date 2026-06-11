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
    resolve_artist,
    resolve_title,
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
    ("男声部 (A)", "A"),
    ("女声部 (B)", "B"),
]

META_LANG_LABELS: list[tuple[str, str]] = [
    ("原文", "origin"),
    ("韩文", "ko"),
    ("英文", "en"),
]


@dataclass
class KscOptions:
    char_bracket: bool = False
    word_bracket: bool = False


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
    return merged


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


def export_song_lyrics(
    song: SongData,
    output_dir: str,
    *,
    lyric_format: LyricFormat,
    part: LyricPart,
    title_lang: str,
    artist_lang: str,
    ksc_options: KscOptions | None = None,
) -> str:
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
