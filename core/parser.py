import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

_CJK_CHAR_RE = re.compile(r"[가-힣一-龥ぁ-んァ-ン]")
_RAP_SECTION_NAME_RE = re.compile(r"^rap\d*$", re.IGNORECASE)
_APOSTROPHES = frozenset("'’‘ʼ")
_EXPLICIT_LYRIC_PUNCT = frozenset(
    "~～!！?？…·•@#$%^&*()（）[]{}|\\/<>`+=，,。.．；;：:「」『』【】_—－-"
)


def strip_lyric_punctuation(text: str) -> str:
    """去除歌词无意义标点（如 ~ ！），保留撇号用于缩写。"""
    chars: list[str] = []
    for ch in text:
        if ch in _APOSTROPHES:
            chars.append(ch)
        elif ch in _EXPLICIT_LYRIC_PUNCT or unicodedata.category(ch).startswith("P"):
            continue
        else:
            chars.append(ch)
    return re.sub(r" +", " ", "".join(chars)).strip()


def normalize_lyric_text(text: str) -> str | None:
    """解析歌词字段：去标点并保留英文词尾空格。"""
    if not text or not text.strip():
        return None
    trailing_space = text.endswith(" ")
    core = strip_lyric_punctuation(text)
    if not core:
        return None
    return core + (" " if trailing_space else "")


@dataclass
class Note:
    start: int
    end: int
    key: int
    is_part_a: bool
    is_part_b: bool


@dataclass
class LyricWord:
    start: int
    end: int
    text: str


@dataclass
class MergedLyricWord:
    start: int
    end: int
    text: str
    syllables: list[LyricWord] = field(default_factory=list)


@dataclass
class LyricLine:
    start: int
    end: int
    text: str
    units: list[LyricWord] = field(default_factory=list)


@dataclass
class SongSection:
    name: str
    start: int
    end: int
    seq: int = 0
    part_a: bool = False
    part_b: bool = False
    highlight: bool = False


@dataclass
class SectionExportInfo:
    name: str
    seq: int
    section_start_ms: int
    section_end_ms: int
    first_line_text: str
    last_line_text: str


@dataclass
class SongData:
    source_path: str
    mr_id: int
    title: str
    title_origin: str = ""
    title_ko: str = ""
    title_en: str = ""
    artist_origin: str = ""
    artist_ko: str = ""
    artist_en: str = ""
    notes: list[Note] = field(default_factory=list)
    words_part_a: list[LyricWord] = field(default_factory=list)
    words_part_b: list[LyricWord] = field(default_factory=list)
    merged_words_part_a: list[MergedLyricWord] = field(default_factory=list)
    merged_words_part_b: list[MergedLyricWord] = field(default_factory=list)
    lines_part_a: list[LyricLine] = field(default_factory=list)
    lines_part_b: list[LyricLine] = field(default_factory=list)
    sections: list[SongSection] = field(default_factory=list)
    section_export_infos: list[SectionExportInfo] = field(default_factory=list)
    tempo_bpm: float = 120.0


def _field_value(data: dict[str, Any], prefix: str, lang: str) -> str:
    return str(data.get(f"{prefix}_{lang}", "") or "").strip()


def resolve_title(song: SongData, lang: str) -> str:
    mapping = {
        "origin": song.title_origin,
        "ko": song.title_ko,
        "en": song.title_en,
    }
    return mapping.get(lang) or song.title or str(song.mr_id)


def resolve_artist(song: SongData, lang: str) -> str:
    if lang == "origin":
        return song.artist_origin
    if lang == "ko":
        return song.artist_ko
    if lang == "en":
        return song.artist_en
    return song.artist_origin


def _safe_title(data: dict[str, Any]) -> str:
    for key in ("title_origin", "title_ko", "title_en", "mr_id"):
        value = data.get(key)
        if value:
            return str(value)
    return "unknown"


def _parse_notes(raw_notes: list[dict]) -> list[Note]:
    return [
        Note(
            start=n["start"],
            end=n["end"],
            key=n["key"],
            is_part_a=bool(n.get("isPartA")),
            is_part_b=bool(n.get("isPartB")),
        )
        for n in raw_notes
    ]


def _extract_words(sections: list[dict], part_key: str, lyric_field: str) -> list[LyricWord]:
    words: list[LyricWord] = []
    for section in sections:
        if not section.get(part_key):
            continue
        for line in section.get("line", []):
            for word in line.get("word", []):
                text = normalize_lyric_text(str(word.get(lyric_field, "")))
                if text is None:
                    continue
                words.append(
                    LyricWord(
                        start=word["start"],
                        end=word["end"],
                        text=text,
                    )
                )
    words.sort(key=lambda w: (w.start, w.end))
    return words


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_CHAR_RE.search(text))


def _append_cjk_word_groups(
    groups: list[MergedLyricWord],
    word: dict,
    lyric_field: str,
) -> None:
    text = normalize_lyric_text(str(word.get(lyric_field, "")))
    if text is None:
        return
    start, end = int(word["start"]), int(word["end"])
    chars = [ch for ch in text if ch.strip() and strip_lyric_punctuation(ch)]
    if not chars:
        return
    if len(chars) == 1:
        syllable = LyricWord(start=start, end=end, text=chars[0])
        groups.append(MergedLyricWord(start, end, chars[0], [syllable]))
        return

    duration = max(1, end - start)
    for index, char in enumerate(chars):
        char_start = start + (duration * index) // len(chars)
        char_end = (
            start + (duration * (index + 1)) // len(chars)
            if index < len(chars) - 1
            else end
        )
        if char_end <= char_start:
            char_end = char_start + 1
        syllable = LyricWord(start=char_start, end=char_end, text=char)
        groups.append(MergedLyricWord(char_start, char_end, char, [syllable]))


def _extract_merged_words(
    sections: list[dict], part_key: str, lyric_field: str
) -> list[MergedLyricWord]:
    """同一行内合并英文音节为单词；行尾无空格也断词；CJK 始终单字。"""
    groups: list[MergedLyricWord] = []
    for section in sections:
        if not section.get(part_key):
            continue
        for line in section.get("line", []):
            buffer_texts: list[str] = []
            buffer_syllables: list[LyricWord] = []
            buffer_start: int | None = None
            buffer_end: int | None = None

            def flush() -> None:
                nonlocal buffer_texts, buffer_syllables, buffer_start, buffer_end
                if not buffer_syllables or buffer_start is None or buffer_end is None:
                    return
                groups.append(
                    MergedLyricWord(
                        start=buffer_start,
                        end=buffer_end,
                        text="".join(buffer_texts),
                        syllables=list(buffer_syllables),
                    )
                )
                buffer_texts = []
                buffer_syllables = []
                buffer_start = None
                buffer_end = None

            for word in line.get("word", []):
                text = normalize_lyric_text(str(word.get(lyric_field, "")))
                if text is None:
                    continue
                if _contains_cjk(text):
                    flush()
                    _append_cjk_word_groups(groups, word, lyric_field)
                    continue

                syllable = LyricWord(
                    start=word["start"],
                    end=word["end"],
                    text=text,
                )
                if buffer_start is None:
                    buffer_start = syllable.start
                buffer_texts.append(text)
                buffer_syllables.append(syllable)
                buffer_end = syllable.end
                if text.endswith(" "):
                    flush()
            flush()
    groups.sort(key=lambda group: (group.start, group.end))
    return groups


def _split_cjk_chars(word: LyricWord) -> list[LyricWord]:
    chars = [
        ch
        for ch in word.text
        if ch.strip() and strip_lyric_punctuation(ch)
    ]
    if not chars:
        return []
    if len(chars) == 1:
        return [LyricWord(word.start, word.end, chars[0])]
    duration = max(1, word.end - word.start)
    result: list[LyricWord] = []
    for index, char in enumerate(chars):
        start = word.start + (duration * index) // len(chars)
        end = (
            word.start + (duration * (index + 1)) // len(chars)
            if index < len(chars) - 1
            else word.end
        )
        if end <= start:
            end = start + 1
        result.append(LyricWord(start, end, char))
    return result


def _syllables_to_units(syllables: list[LyricWord]) -> list[LyricWord]:
    units: list[LyricWord] = []
    buffer: list[LyricWord] = []

    def flush() -> None:
        if not buffer:
            return
        units.append(
            LyricWord(
                start=buffer[0].start,
                end=buffer[-1].end,
                text="".join(item.text for item in buffer).strip(),
            )
        )
        buffer.clear()

    for syllable in syllables:
        text = syllable.text.strip()
        if not text:
            continue
        if _contains_cjk(syllable.text):
            flush()
            units.extend(_split_cjk_chars(syllable))
            continue
        buffer.append(LyricWord(syllable.start, syllable.end, text))
        if syllable.text.endswith(" "):
            flush()
    flush()
    return [unit for unit in units if unit.text.strip()]


def _units_to_line_text(units: list[LyricWord]) -> str:
    """CJK 连续拼接；英文单词之间自动加空格，句末单词不加尾部空格。"""
    parts: list[str] = []
    for unit in units:
        text = unit.text.strip()
        if not text:
            continue
        if not _contains_cjk(text) and parts:
            parts.append(" ")
        parts.append(text)
    return "".join(parts)


def _extract_lines(sections: list[dict], part_key: str, lyric_field: str) -> list[LyricLine]:
    """从 JSON 的 section.line 提取分句；每行含整句文本与字级时间单元。"""
    lines: list[LyricLine] = []
    for section in sections:
        if not section.get(part_key):
            continue
        for line in section.get("line", []):
            syllables: list[LyricWord] = []
            for word in line.get("word", []):
                text = normalize_lyric_text(str(word.get(lyric_field, "")))
                if text is None:
                    continue
                syllables.append(
                    LyricWord(
                        start=int(word["start"]),
                        end=int(word["end"]),
                        text=text,
                    )
                )
            if not syllables:
                continue
            units = _syllables_to_units(syllables)
            if not units:
                continue
            lines.append(
                LyricLine(
                    start=int(line.get("start", syllables[0].start)),
                    end=int(line.get("end", syllables[-1].end)),
                    text=_units_to_line_text(units),
                    units=units,
                )
            )
    lines.sort(key=lambda item: (item.start, item.end))
    return lines


def _parse_tempo(tempos: list[dict]) -> float:
    if not tempos:
        return 120.0
    return float(tempos[0].get("tempo", 120.0))


def _parse_sections(raw_sections: list[dict]) -> list[SongSection]:
    sections: list[SongSection] = []
    for section in raw_sections:
        name = str(section.get("name", "") or "").strip()
        if not name:
            continue
        sections.append(
            SongSection(
                name=name,
                start=int(section.get("start", 0)),
                end=int(section.get("end", 0)),
                seq=int(section.get("seq", 0)),
                part_a=bool(section.get("partA")),
                part_b=bool(section.get("partB")),
                highlight=bool(section.get("highlight")),
            )
        )
    sections.sort(key=lambda item: (item.seq, item.start, item.end))
    return sections


def format_section_display_name(name: str) -> str:
    name = name.strip()
    if not name:
        return "Section"
    return name[0].upper() + name[1:]


def _parse_section_line(line: dict, lyric_field: str) -> LyricLine | None:
    syllables: list[LyricWord] = []
    for word in line.get("word", []):
        text = normalize_lyric_text(str(word.get(lyric_field, "")))
        if text is None:
            continue
        syllables.append(
            LyricWord(
                start=int(word["start"]),
                end=int(word["end"]),
                text=text,
            )
        )
    if not syllables:
        return None
    units = _syllables_to_units(syllables)
    if not units:
        return None
    return LyricLine(
        start=int(line.get("start", syllables[0].start)),
        end=int(line.get("end", syllables[-1].end)),
        text=_units_to_line_text(units),
        units=units,
    )


def extract_section_export_infos(
    sections: list[dict], lyric_field: str
) -> list[SectionExportInfo]:
    infos: list[SectionExportInfo] = []
    for section in sections:
        name = str(section.get("name", "") or "").strip()
        if not name:
            continue
        section_start_ms = int(section.get("start", 0))
        section_end_ms = int(section.get("end", 0))
        parsed_lines: list[LyricLine] = []
        for line in section.get("line", []):
            parsed = _parse_section_line(line, lyric_field)
            if parsed is not None:
                parsed_lines.append(parsed)

        first_line_text = parsed_lines[0].text if parsed_lines else ""
        last_line_text = parsed_lines[-1].text if parsed_lines else ""
        infos.append(
            SectionExportInfo(
                name=name,
                seq=int(section.get("seq", 0)),
                section_start_ms=section_start_ms,
                section_end_ms=section_end_ms,
                first_line_text=first_line_text,
                last_line_text=last_line_text,
            )
        )
    infos.sort(key=lambda item: (item.seq, item.section_start_ms))
    return infos


def shift_time_ms(time_ms: int, offset_ms: int) -> int:
    """整体时间偏移（毫秒）；正数向后，负数向前，结果不小于 0。"""
    if offset_ms == 0:
        return time_ms
    return max(0, time_ms + offset_ms)


def _shift_time_range(start: int, end: int, offset_ms: int) -> tuple[int, int]:
    shifted_start = shift_time_ms(start, offset_ms)
    shifted_end = shift_time_ms(end, offset_ms)
    if shifted_end <= shifted_start:
        shifted_end = shifted_start + 1
    return shifted_start, shifted_end


def _shift_lyric_word(word: LyricWord, offset_ms: int) -> LyricWord:
    start, end = _shift_time_range(word.start, word.end, offset_ms)
    return LyricWord(start=start, end=end, text=word.text)


def _shift_merged_word(word: MergedLyricWord, offset_ms: int) -> MergedLyricWord:
    start, end = _shift_time_range(word.start, word.end, offset_ms)
    return MergedLyricWord(
        start=start,
        end=end,
        text=word.text,
        syllables=[_shift_lyric_word(syllable, offset_ms) for syllable in word.syllables],
    )


def _shift_lyric_line(line: LyricLine, offset_ms: int) -> LyricLine:
    start, end = _shift_time_range(line.start, line.end, offset_ms)
    return LyricLine(
        start=start,
        end=end,
        text=line.text,
        units=[_shift_lyric_word(unit, offset_ms) for unit in line.units],
    )


def _shift_section(section: SongSection, offset_ms: int) -> SongSection:
    start, end = _shift_time_range(section.start, section.end, offset_ms)
    return SongSection(
        name=section.name,
        start=start,
        end=end,
        seq=section.seq,
        part_a=section.part_a,
        part_b=section.part_b,
        highlight=section.highlight,
    )


def _shift_note(note: Note, offset_ms: int) -> Note:
    start, end = _shift_time_range(note.start, note.end, offset_ms)
    return Note(
        start=start,
        end=end,
        key=note.key,
        is_part_a=note.is_part_a,
        is_part_b=note.is_part_b,
    )


def is_rap_section_name(name: str) -> bool:
    return bool(_RAP_SECTION_NAME_RE.match(name.strip()))


def is_rap_section(section: SongSection) -> bool:
    return is_rap_section_name(section.name)


def _overlaps_time_range(start: int, end: int, range_start: int, range_end: int) -> bool:
    return start < range_end and end > range_start


def exclude_rap_sections_from_song(song: SongData) -> SongData:
    """移除 Rap 段落内的音符、歌词，并剔除 Rap 段落标记。"""
    rap_ranges = [(section.start, section.end) for section in song.sections if is_rap_section(section)]
    if not rap_ranges:
        return song

    def in_rap_range(start: int, end: int) -> bool:
        return any(
            _overlaps_time_range(start, end, range_start, range_end)
            for range_start, range_end in rap_ranges
        )

    return SongData(
        source_path=song.source_path,
        mr_id=song.mr_id,
        title=song.title,
        title_origin=song.title_origin,
        title_ko=song.title_ko,
        title_en=song.title_en,
        artist_origin=song.artist_origin,
        artist_ko=song.artist_ko,
        artist_en=song.artist_en,
        notes=[note for note in song.notes if not in_rap_range(note.start, note.end)],
        words_part_a=[
            word for word in song.words_part_a if not in_rap_range(word.start, word.end)
        ],
        words_part_b=[
            word for word in song.words_part_b if not in_rap_range(word.start, word.end)
        ],
        merged_words_part_a=[
            word
            for word in song.merged_words_part_a
            if not in_rap_range(word.start, word.end)
        ],
        merged_words_part_b=[
            word
            for word in song.merged_words_part_b
            if not in_rap_range(word.start, word.end)
        ],
        lines_part_a=[
            line for line in song.lines_part_a if not in_rap_range(line.start, line.end)
        ],
        lines_part_b=[
            line for line in song.lines_part_b if not in_rap_range(line.start, line.end)
        ],
        sections=[section for section in song.sections if not is_rap_section(section)],
        tempo_bpm=song.tempo_bpm,
    )


def _shift_section_export_info(info: SectionExportInfo, offset_ms: int) -> SectionExportInfo:
    section_start_ms, section_end_ms = _shift_time_range(
        info.section_start_ms, info.section_end_ms, offset_ms
    )
    return SectionExportInfo(
        name=info.name,
        seq=info.seq,
        section_start_ms=section_start_ms,
        section_end_ms=section_end_ms,
        first_line_text=info.first_line_text,
        last_line_text=info.last_line_text,
    )


def apply_song_time_offset(song: SongData, offset_ms: int) -> SongData:
    """对歌曲全部音符与歌词时间做整体偏移。"""
    if offset_ms == 0:
        return song

    return SongData(
        source_path=song.source_path,
        mr_id=song.mr_id,
        title=song.title,
        title_origin=song.title_origin,
        title_ko=song.title_ko,
        title_en=song.title_en,
        artist_origin=song.artist_origin,
        artist_ko=song.artist_ko,
        artist_en=song.artist_en,
        notes=[_shift_note(note, offset_ms) for note in song.notes],
        words_part_a=[_shift_lyric_word(word, offset_ms) for word in song.words_part_a],
        words_part_b=[_shift_lyric_word(word, offset_ms) for word in song.words_part_b],
        merged_words_part_a=[
            _shift_merged_word(word, offset_ms) for word in song.merged_words_part_a
        ],
        merged_words_part_b=[
            _shift_merged_word(word, offset_ms) for word in song.merged_words_part_b
        ],
        lines_part_a=[_shift_lyric_line(line, offset_ms) for line in song.lines_part_a],
        lines_part_b=[_shift_lyric_line(line, offset_ms) for line in song.lines_part_b],
        sections=[_shift_section(section, offset_ms) for section in song.sections],
        section_export_infos=[
            _shift_section_export_info(info, offset_ms)
            for info in song.section_export_infos
        ],
        tempo_bpm=song.tempo_bpm,
    )


def is_valid_ms_json(data: dict[str, Any]) -> bool:
    mnote = data.get("mnote")
    if not isinstance(mnote, dict):
        return False
    return isinstance(mnote.get("note"), list) and isinstance(mnote.get("section"), list)


def load_song_json(path: str, lyric_field: str = "ori") -> SongData:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not is_valid_ms_json(data):
        raise ValueError(f"不是有效的 MS JSON 文件: {path}")

    mnote = data["mnote"]
    sections = mnote.get("section", [])

    return SongData(
        source_path=path,
        mr_id=int(data.get("mr_id", 0)),
        title=_safe_title(data),
        title_origin=_field_value(data, "title", "origin"),
        title_ko=_field_value(data, "title", "ko"),
        title_en=_field_value(data, "title", "en"),
        artist_origin=_field_value(data, "artist_names", "origin"),
        artist_ko=_field_value(data, "artist_names", "ko"),
        artist_en=_field_value(data, "artist_names", "en"),
        notes=_parse_notes(mnote["note"]),
        words_part_a=_extract_words(sections, "partA", lyric_field),
        words_part_b=_extract_words(sections, "partB", lyric_field),
        merged_words_part_a=_extract_merged_words(sections, "partA", lyric_field),
        merged_words_part_b=_extract_merged_words(sections, "partB", lyric_field),
        lines_part_a=_extract_lines(sections, "partA", lyric_field),
        lines_part_b=_extract_lines(sections, "partB", lyric_field),
        sections=_parse_sections(sections),
        section_export_infos=extract_section_export_infos(sections, lyric_field),
        tempo_bpm=_parse_tempo(mnote.get("tempos", [])),
    )


def collect_json_files(path: str, *, valid_only: bool = False) -> list[str]:
    """扫描路径下所有 JSON；valid_only=True 时仅返回含 mnote 的有效文件。"""
    files = scan_json_files(path)
    if not valid_only:
        return files

    valid_files: list[str] = []
    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if is_valid_ms_json(data):
                valid_files.append(file_path)
        except Exception:
            continue
    return valid_files


def scan_json_files(path: str) -> list[str]:
    path = os.path.abspath(path)
    if os.path.isfile(path):
        if path.lower().endswith(".json"):
            return [path]
        return []

    if not os.path.isdir(path):
        return []

    results: list[str] = []
    for root, _, files in os.walk(path):
        for name in files:
            if name.lower().endswith(".json"):
                results.append(os.path.join(root, name))
    results.sort()
    return results
