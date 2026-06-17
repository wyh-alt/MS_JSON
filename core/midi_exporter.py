import os
import re
from typing import TYPE_CHECKING, Literal

import mido

if TYPE_CHECKING:
    from core.audio_calibration import AudioCalibrationResult

from core.parser import (
    LyricWord,
    MergedLyricWord,
    Note,
    SongData,
    SongSection,
    apply_song_time_offset,
    exclude_non_melody_notes_from_song,
    exclude_rap_sections_from_song,
    load_song_json,
    strip_lyric_punctuation,
)

TICKS_PER_BEAT = 480
DEFAULT_TEMPO_BPM = 120.0
EXTENSION_SYLLABLE = "-"
TIME_TOLERANCE_MS = 150
OCTAVE_SEMITONES = 12
NoteSignature = tuple[int, int, int]

PartMode = Literal[
    "separate",
    "merge_same",
    "merge_multi",
    "male_only",
    "female_only",
    "lyric_lang_split",
]
LyricGranularity = Literal["syllable", "word", "word_syllable"]
LyricScriptCategory = Literal["digit", "latin", "han", "hangul", "kana"]

PART_MODE_LABELS: list[tuple[str, PartMode]] = [
    ("合并导出（同轨）", "merge_same"),
    ("合并导出（分轨）", "merge_multi"),
    ("分别导出", "separate"),
    ("歌词语种分轨", "lyric_lang_split"),
    ("仅A声部", "male_only"),
    ("仅B声部", "female_only"),
]

LYRIC_SCRIPT_ORDER: list[LyricScriptCategory] = [
    "digit",
    "latin",
    "han",
    "hangul",
    "kana",
]

LYRIC_SCRIPT_TRACK_LABELS: dict[LyricScriptCategory, str] = {
    "digit": "数字",
    "latin": "英文",
    "han": "汉字",
    "hangul": "韩文",
    "kana": "假名",
}

LYRIC_GRANULARITY_LABELS: list[tuple[str, LyricGranularity]] = [
    ("单词级（英文整词，后续 -）", "word"),
    ("音节级（按词音节数#序号）", "word_syllable"),
    ("与 JSON 条目一致", "syllable"),
]

_ENGLISH_SYLLABLE_PART_RE = re.compile(
    r"[^aeiouy]*[aeiouy]+(?:[^aeiouy](?![aeiouy]|$))?",
    re.IGNORECASE,
)

_CJK_CHAR_RE = re.compile(r"[가-힣一-龥ぁ-んァ-ン]")
_HANGUL_CHAR_RE = re.compile(r"[가-힣]")
_HAN_CHAR_RE = re.compile(r"[一-龥]")
_KANA_CHAR_RE = re.compile(r"[ぁ-んァ-ン]")
_LATIN_CHAR_RE = re.compile(r"[a-zA-Z]")
_DIGIT_CHAR_RE = re.compile(r"[0-9]")
_WORD_SYLLABLE_INDEX_RE = re.compile(r"#\d+ ?$")


def _classify_lyric_script(text: str) -> LyricScriptCategory | None:
    """按原文字符脚本分类；纯数字、拉丁、汉字、韩文、假名分轨。"""
    if text == EXTENSION_SYLLABLE:
        return None
    core = _strip_lyric_punctuation(text).strip()
    if not core:
        return None
    core = _WORD_SYLLABLE_INDEX_RE.sub("", core).strip()
    if not core:
        return None
    if _DIGIT_CHAR_RE.fullmatch(r"[0-9]+"):
        return "digit"

    scripts: set[LyricScriptCategory] = set()
    for char in core:
        if char.isspace():
            continue
        if _DIGIT_CHAR_RE.match(char):
            scripts.add("digit")
        elif _LATIN_CHAR_RE.match(char):
            scripts.add("latin")
        elif _HANGUL_CHAR_RE.match(char):
            scripts.add("hangul")
        elif _HAN_CHAR_RE.match(char):
            scripts.add("han")
        elif _KANA_CHAR_RE.match(char):
            scripts.add("kana")

    if not scripts:
        return None
    if len(scripts) == 1:
        return next(iter(scripts))

    for char in core:
        if char.isspace():
            continue
        for script, pattern in (
            ("digit", _DIGIT_CHAR_RE),
            ("latin", _LATIN_CHAR_RE),
            ("hangul", _HANGUL_CHAR_RE),
            ("han", _HAN_CHAR_RE),
            ("kana", _KANA_CHAR_RE),
        ):
            if pattern.match(char):
                return script
    return None


def _resolve_note_script_categories(
    sorted_notes: list[Note],
    lyric_map: dict[NoteSignature, str],
) -> dict[NoteSignature, LyricScriptCategory]:
    """为每个带歌词音符确定语种；延音符继承前一词条语种。"""
    categories: dict[NoteSignature, LyricScriptCategory] = {}
    active_script: LyricScriptCategory | None = None
    for note in sorted_notes:
        signature = _note_signature(note)
        text = lyric_map.get(signature)
        if text is None:
            continue
        if text == EXTENSION_SYLLABLE:
            if active_script is not None:
                categories[signature] = active_script
            continue
        script = _classify_lyric_script(text)
        if script is None:
            continue
        active_script = script
        categories[signature] = script
    return categories


def _merged_lyric_map_for_notes(
    song: SongData,
    notes: list[Note],
    lyric_granularity: LyricGranularity,
) -> dict[NoteSignature, str]:
    notes_a = filter_notes(song.notes, "A")
    notes_b = filter_notes(song.notes, "B")
    notes_other = filter_notes(song.notes, "O")
    combined = sorted(
        _dedupe_notes(notes_a + notes_b + notes_other),
        key=lambda n: (n.start, n.end, n.key),
    )
    raw_map = _merge_lyrics_by_signature(
        song,
        notes_a,
        notes_b,
        lyric_granularity,
        notes_other=notes_other,
    )
    return _format_ktv_lyric_map(raw_map)


def _collect_tagged_words_for_script(
    song: SongData,
    script: LyricScriptCategory,
    lyric_granularity: LyricGranularity,
) -> list[tuple[str, LyricWord]]:
    tagged: list[tuple[str, LyricWord]] = []
    for part in ("A", "B"):
        for word in _collect_words_for_part(song, part, lyric_granularity):
            if _classify_lyric_script(word.text) == script:
                tagged.append((part, word))
    return tagged


def _collect_tagged_groups_for_script(
    song: SongData,
    script: LyricScriptCategory,
) -> list[tuple[str, MergedLyricWord]]:
    tagged: list[tuple[str, MergedLyricWord]] = []
    for part in ("A", "B"):
        for group in _part_merged_words(song, part):
            text = _normalize_lyric_text(group.text)
            if text is None or _classify_lyric_script(text) != script:
                continue
            tagged.append((part, group))
    return tagged


def _scripts_present_in_song(
    song: SongData,
    lyric_granularity: LyricGranularity,
) -> list[LyricScriptCategory]:
    scripts: set[LyricScriptCategory] = set()
    for part in ("A", "B"):
        for word in _collect_words_for_part(song, part, lyric_granularity):
            category = _classify_lyric_script(word.text)
            if category is not None:
                scripts.add(category)
    return [script for script in LYRIC_SCRIPT_ORDER if script in scripts]


def _script_track_lyrics_and_notes(
    song: SongData,
    sorted_combined: list[Note],
    script: LyricScriptCategory,
    lyric_granularity: LyricGranularity,
) -> tuple[list[Note], dict[NoteSignature, str]]:
    """按语种单独匹配歌词，延音符仅在同声部（含 Other）链上延伸。"""
    part_boundaries = {
        part: _part_word_first_indices(
            sorted_combined, song, part, lyric_granularity
        )
        for part in ("A", "B")
    }
    if lyric_granularity == "word_syllable":
        tagged_groups = _collect_tagged_groups_for_script(song, script)
        index_map = _assign_tagged_merged_syllables_to_note_indices(
            sorted_combined, tagged_groups, part_boundaries=part_boundaries
        )
    else:
        tagged = _collect_tagged_words_for_script(song, script, lyric_granularity)
        index_map = _assign_tagged_words_to_note_indices(
            sorted_combined, tagged, part_boundaries=part_boundaries
        )
    if not index_map:
        return [], {}
    script_notes = [sorted_combined[index] for index in sorted(index_map)]
    lyric_map = _format_ktv_lyric_map(
        {
            _note_signature(sorted_combined[index]): text
            for index, text in index_map.items()
        }
    )
    return script_notes, lyric_map


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name.strip() or "unknown"


def _ms_to_ticks(ms: int, tempo_us: int, ticks_per_beat: int) -> int:
    if ms <= 0:
        return 0
    return int(round(mido.second2tick(ms / 1000.0, ticks_per_beat, tempo_us)))


def _resolve_tick_tempo(song: SongData, write_tempo: bool) -> tuple[float, int]:
    bpm = song.tempo_bpm if write_tempo else DEFAULT_TEMPO_BPM
    return bpm, mido.bpm2tempo(bpm)


def _abs_ticks(ms: int, tempo_us: int, ticks_per_beat: int) -> int:
    return _ms_to_ticks(ms, tempo_us, ticks_per_beat)


def filter_notes(notes: list[Note], part: str) -> list[Note]:
    if part == "A":
        return [n for n in notes if n.is_part_a]
    if part == "B":
        return [n for n in notes if n.is_part_b]
    if part == "O":
        return [n for n in notes if not n.is_part_a and not n.is_part_b]
    raise ValueError(f"未知声部: {part}")


def _word_note_overlap(word: LyricWord, note: Note) -> int:
    return max(0, min(word.end, note.end) - max(word.start, note.start))


def _is_lyric_fragment(text: str) -> bool:
    stripped = text.strip()
    return stripped in ("'", "'", "'") or bool(re.fullmatch(r"[a-zA-Z]", stripped))


def _ranked_notes_for_word(word: LyricWord, notes: list[Note]) -> list[Note]:
    """按音符起始时间与词起始时间的接近程度排序候选音符。"""
    tol = TIME_TOLERANCE_MS
    word_duration = max(1, word.end - word.start)
    min_overlap = min(50, int(word_duration * 0.3))
    scored: list[tuple[tuple[int, int, float], Note]] = []

    for note in notes:
        overlap = _word_note_overlap(word, note)
        if overlap < min_overlap:
            continue
        start_diff = abs(note.start - word.start)
        if start_diff > tol:
            continue
        end_diff = max(0, abs(note.end - word.end) - tol)
        coverage = overlap / word_duration
        scored.append(((start_diff, end_diff, -coverage), note))

    scored.sort(key=lambda item: item[0])
    return [note for _, note in scored]


def _notes_for_part(notes: list[Note], part: str) -> list[Note]:
    if part == "A":
        return [note for note in notes if note.is_part_a]
    if part == "B":
        return [note for note in notes if note.is_part_b]
    raise ValueError(f"未知声部: {part}")


def _first_note_index_for_word(
    word: LyricWord,
    sorted_notes: list[Note],
    note_index: dict[int, int],
    *,
    part: str | None = None,
) -> int | None:
    if part in ("A", "B"):
        candidates = _notes_for_part(sorted_notes, part)
    else:
        candidates = sorted_notes
    ranked = _ranked_notes_for_word(word, candidates)
    if not ranked:
        return None
    return note_index[id(ranked[0])]


def _collect_words_for_part(
    song: SongData,
    part: str,
    lyric_granularity: LyricGranularity,
) -> list[LyricWord]:
    if lyric_granularity == "word":
        words: list[LyricWord] = []
        for group in _part_merged_words(song, part):
            text = _normalize_lyric_text(group.text)
            if text is None:
                continue
            words.append(LyricWord(group.start, group.end, text))
        return words
    return _prepare_syllable_words(_part_syllable_words(song, part))


def _next_distinct_word_first_index(
    first_indices: list[int | None],
    word_index: int,
    current_first_idx: int,
) -> int | None:
    """下一字首音索引；跳过与当前首音相同的多声部重复词条。"""
    for later_index in range(word_index + 1, len(first_indices)):
        candidate = first_indices[later_index]
        if candidate is None or candidate == current_first_idx:
            continue
        return candidate
    return None


def _part_word_first_indices(
    sorted_notes: list[Note],
    song: SongData,
    part: str,
    lyric_granularity: LyricGranularity,
) -> list[int]:
    """同声部全部词条（不限语种）的首音索引，供语种分轨时截断延音符链。"""
    note_index = {id(note): index for index, note in enumerate(sorted_notes)}
    indices: list[int] = []
    if lyric_granularity == "word_syllable":
        for group in _part_merged_words(song, part):
            text = _normalize_lyric_text(group.text)
            if text is None:
                continue
            word = LyricWord(group.start, group.end, text)
            first_idx = _first_note_index_for_word(
                word, sorted_notes, note_index, part=part
            )
            if first_idx is not None:
                indices.append(first_idx)
    else:
        for word in _collect_words_for_part(song, part, lyric_granularity):
            first_idx = _first_note_index_for_word(
                word, sorted_notes, note_index, part=part
            )
            if first_idx is not None:
                indices.append(first_idx)
    return sorted(set(indices))


def _next_part_boundary_index(
    part: str,
    current_first_idx: int,
    part_boundaries: dict[str, list[int]],
) -> int | None:
    for candidate in part_boundaries.get(part, []):
        if candidate > current_first_idx:
            return candidate
    return None


def _assign_tagged_words_to_note_indices(
    sorted_notes: list[Note],
    tagged_words: list[tuple[str, LyricWord]],
    *,
    part_boundaries: dict[str, list[int]] | None = None,
) -> dict[int, str]:
    """首音按声部匹配，延音符沿合并轨连续延伸，止于下一字首音。"""
    if not tagged_words:
        return {}

    note_index = {id(note): index for index, note in enumerate(sorted_notes)}
    words_sorted = sorted(
        tagged_words,
        key=lambda item: (item[1].start, item[1].end, item[1].text),
    )
    first_indices: list[int | None] = [
        _first_note_index_for_word(
            word,
            sorted_notes,
            note_index,
            part=part if part in ("A", "B") else None,
        )
        for part, word in words_sorted
    ]

    lyric_map: dict[int, str] = {}
    for word_index, (part, word) in enumerate(words_sorted):
        first_idx = first_indices[word_index]
        if first_idx is None:
            continue

        if part_boundaries is not None and part in part_boundaries:
            next_first_idx = _next_part_boundary_index(
                part, first_idx, part_boundaries
            )
        else:
            next_first_idx = _next_distinct_word_first_index(
                first_indices, word_index, first_idx
            )

        chain = _extend_contiguous_note_indices(
            sorted_notes,
            first_idx,
            next_first_idx,
            part=part if part in ("A", "B") else None,
        )

        if first_idx in lyric_map:
            existing = lyric_map[first_idx]
            if existing == EXTENSION_SYLLABLE:
                lyric_map[first_idx] = word.text
            elif _is_lyric_fragment(word.text):
                lyric_map[first_idx] += word.text
            elif existing.rstrip() == word.text.rstrip():
                pass
            else:
                continue
        else:
            lyric_map[first_idx] = word.text

        for idx in chain[1:]:
            if idx in lyric_map and lyric_map[idx] != EXTENSION_SYLLABLE:
                break
            lyric_map[idx] = EXTENSION_SYLLABLE

    return _refine_lyric_index_map(lyric_map)


def _assign_words_to_note_indices(
    sorted_notes: list[Note],
    words: list[LyricWord],
    *,
    part: str | None = None,
) -> dict[int, str]:
    """以音符时间为准：首音写字，连续后续音写 -，至下一字首音前停止。"""
    if not words:
        return {}
    tagged = [(part or "", word) for word in words]
    return _assign_tagged_words_to_note_indices(sorted_notes, tagged)


def _assign_tagged_merged_syllables_to_note_indices(
    sorted_notes: list[Note],
    tagged_groups: list[tuple[str, MergedLyricWord]],
    *,
    part_boundaries: dict[str, list[int]] | None = None,
) -> dict[int, str]:
    """单词级合并后，按词的实际音节数在音符链上依次写入 word#1、word#2…"""
    if not tagged_groups:
        return {}

    note_index = {id(note): index for index, note in enumerate(sorted_notes)}
    groups_sorted = sorted(
        tagged_groups,
        key=lambda item: (item[1].start, item[1].end, item[1].text),
    )
    first_indices: list[int | None] = []
    for part, group in groups_sorted:
        word = LyricWord(group.start, group.end, group.text)
        first_indices.append(
            _first_note_index_for_word(
                word,
                sorted_notes,
                note_index,
                part=part if part in ("A", "B") else None,
            )
        )

    lyric_map: dict[int, str] = {}
    for group_index, (part, group) in enumerate(groups_sorted):
        merged_text = _normalize_lyric_text(group.text)
        if merged_text is None:
            continue

        first_idx = first_indices[group_index]
        if first_idx is None:
            continue

        if part_boundaries is not None and part in part_boundaries:
            next_first_idx = _next_part_boundary_index(
                part, first_idx, part_boundaries
            )
        else:
            next_first_idx = _next_distinct_word_first_index(
                first_indices, group_index, first_idx
            )
        chain = _extend_contiguous_note_indices(
            sorted_notes,
            first_idx,
            next_first_idx,
            part=part if part in ("A", "B") else None,
        )
        labels = _word_syllable_labels(merged_text)
        if not labels:
            continue

        if first_idx in lyric_map:
            existing = lyric_map[first_idx]
            if existing == EXTENSION_SYLLABLE:
                pass
            elif existing.rstrip() == merged_text.rstrip() or existing in labels:
                pass
            else:
                continue

        for note_pos, note_idx in enumerate(chain):
            label = labels[note_pos] if note_pos < len(labels) else EXTENSION_SYLLABLE
            if note_idx in lyric_map and lyric_map[note_idx] != EXTENSION_SYLLABLE:
                if lyric_map[note_idx] == label:
                    continue
                if label == EXTENSION_SYLLABLE:
                    break
                if note_pos == 0:
                    lyric_map[note_idx] = label
                    continue
                break
            lyric_map[note_idx] = label

    return _refine_lyric_index_map(lyric_map)


def _is_other_note(note: Note) -> bool:
    return not note.is_part_a and not note.is_part_b


def _extend_contiguous_note_indices(
    sorted_notes: list[Note],
    first_idx: int,
    next_first_idx: int | None,
    *,
    part: str | None = None,
) -> list[int]:
    """从首音起沿相对连续音符延伸，止于下一字首音或与前音间隔超过阈值。"""
    boundary = len(sorted_notes) if next_first_idx is None else next_first_idx
    chain = [first_idx]
    for idx in range(first_idx + 1, boundary):
        note = sorted_notes[idx]
        if note.start - sorted_notes[chain[-1]].end > TIME_TOLERANCE_MS:
            break
        if part in ("A", "B"):
            if part == "A" and not (note.is_part_a or _is_other_note(note)):
                continue
            if part == "B" and not (note.is_part_b or _is_other_note(note)):
                continue
        chain.append(idx)
    return chain


def _match_word_to_notes(word: LyricWord, sorted_notes: list[Note]) -> list[Note] | None:
    """匹配一词对应的连续音符链（供需要音符列表的调用方使用）。"""
    note_index = {id(note): index for index, note in enumerate(sorted_notes)}
    first_idx = _first_note_index_for_word(word, sorted_notes, note_index)
    if first_idx is None:
        return None
    chain_indices = _extend_contiguous_note_indices(
        sorted_notes,
        first_idx,
        len(sorted_notes),
    )
    return [sorted_notes[index] for index in chain_indices]


def _strip_lyric_punctuation(text: str) -> str:
    return strip_lyric_punctuation(text)


def _normalize_lyric_text(text: str) -> str | None:
    if text == EXTENSION_SYLLABLE:
        return EXTENSION_SYLLABLE
    trailing_space = text.endswith(" ")
    core = _strip_lyric_punctuation(text)
    if not core:
        return None
    return core + (" " if trailing_space else "")


def _refine_lyric_index_map(index_map: dict[int, str]) -> dict[int, str]:
    """清理标注括号，合并撇号/单字音节碎片，避免出现 -plz、'、unds) 等。"""
    items: list[tuple[int, str]] = []
    for i in sorted(index_map):
        text = _normalize_lyric_text(index_map[i])
        if text is None:
            continue
        items.append((i, text))

    merged: list[tuple[int, str]] = []
    for i, text in items:
        if text in ("'", "'", "'") and merged:
            prev_i, prev_text = merged[-1]
            if prev_text != EXTENSION_SYLLABLE:
                merged[-1] = (prev_i, prev_text.rstrip() + text.strip())
                continue
        if (
            text != EXTENSION_SYLLABLE
            and re.fullmatch(r"[a-zA-Z] ?", text.strip())
            and merged
        ):
            prev_i, prev_text = merged[-1]
            if prev_text.rstrip().endswith(("'", "'", "'")):
                merged[-1] = (prev_i, prev_text.rstrip() + text.strip())
                continue
        merged.append((i, text))

    return dict(merged)


def map_lyrics_to_notes(
    notes: list[Note], words: list[LyricWord], *, part: str | None = None
) -> dict[int, str]:
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    return _assign_words_to_note_indices(sorted_notes, words, part=part)


def map_merged_word_lyrics_to_notes(
    notes: list[Note],
    word_groups: list[MergedLyricWord],
    *,
    part: str | None = None,
) -> dict[int, str]:
    """单词级：整词写在首音，同一词跨多音时后续音符写 -。"""
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    words: list[LyricWord] = []
    for group in word_groups:
        text = _normalize_lyric_text(group.text)
        if text is None:
            continue
        words.append(LyricWord(group.start, group.end, text))
    return _assign_words_to_note_indices(sorted_notes, words, part=part)


def map_merged_word_syllable_lyrics_to_notes(
    notes: list[Note],
    word_groups: list[MergedLyricWord],
    *,
    part: str | None = None,
) -> dict[int, str]:
    """音节级：按词的实际音节数在音符链上依次写入 word#1、word#2…"""
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    tagged = [(part or "", group) for group in word_groups]
    return _assign_tagged_merged_syllables_to_note_indices(sorted_notes, tagged)


def _part_syllable_words(song: SongData, part: str) -> list[LyricWord]:
    if part == "A":
        return song.words_part_a
    if part == "B":
        return song.words_part_b
    raise ValueError(f"未知声部: {part}")


def _split_cjk_units(text: str) -> list[str]:
    core = _strip_lyric_punctuation(text).strip()
    if not core:
        return []
    if _CJK_CHAR_RE.search(core):
        return [ch for ch in core if ch.strip()]
    trailing_space = text.endswith(" ")
    return [core + (" " if trailing_space else "")]


def _is_cjk_text(text: str) -> bool:
    return bool(_CJK_CHAR_RE.search(_strip_lyric_punctuation(text)))


def _prepare_syllable_words(words: list[LyricWord]) -> list[LyricWord]:
    """韩/中文等 CJK 始终拆为单字；英文等拉丁文保持 JSON 条目不变。"""
    expanded: list[LyricWord] = []
    for word in words:
        units = _split_cjk_units(word.text)
        if not units:
            continue
        if len(units) == 1:
            normalized = _normalize_lyric_text(units[0])
            if normalized is None:
                continue
            expanded.append(LyricWord(word.start, word.end, normalized))
            continue

        duration = max(1, word.end - word.start)
        for index, unit in enumerate(units):
            start = word.start + (duration * index) // len(units)
            end = (
                word.start + (duration * (index + 1)) // len(units)
                if index < len(units) - 1
                else word.end
            )
            if end <= start:
                end = start + 1
            expanded.append(LyricWord(start, end, unit))
    return expanded


def _word_syllable_label(merged_text: str, syllable_index: int, syllable_count: int) -> str:
    """多音节英文整词显示为 word#1、word#2；单音节或 CJK 保持原文。"""
    if syllable_count <= 1 or _is_cjk_text(merged_text):
        return merged_text
    core = merged_text.rstrip()
    trailing_space = merged_text.endswith(" ")
    suffix = " " if trailing_space and syllable_index == syllable_count - 1 else ""
    return f"{core}#{syllable_index + 1}{suffix}"


def _word_syllable_labels(merged_text: str) -> list[str]:
    syllable_count = len(_split_linguistic_syllables(merged_text))
    if syllable_count <= 0:
        return []
    return [
        _word_syllable_label(merged_text, index, syllable_count)
        for index in range(syllable_count)
    ]


def _adjust_syllable_parts(parts: list[str], count: int) -> list[str]:
    merged = [part for part in parts if part]
    if not merged or count <= 0:
        return []
    while len(merged) > count:
        merged[-2] = merged[-2] + merged[-1]
        merged.pop()
    while len(merged) < count:
        index = max(range(len(merged)), key=lambda i: len(merged[i]))
        piece = merged.pop(index)
        if len(piece) < 2:
            merged.insert(index, piece)
            break
        mid = len(piece) // 2
        merged.insert(index, piece[:mid])
        merged.insert(index + 1, piece[mid:])
    return merged if len(merged) == count else []


def _pyphen_syllable_parts(word: str) -> list[str]:
    try:
        import pyphen

        return [
            part
            for part in pyphen.Pyphen(lang="en_US").inserted(word.lower()).split("-")
            if part
        ]
    except Exception:
        return []


def _split_english_syllable_parts(word: str) -> list[str]:
    import syllables

    core = word.strip()
    if not core:
        return []

    pyphen_parts = _pyphen_syllable_parts(core)
    if len(pyphen_parts) > 1:
        return pyphen_parts

    estimate = syllables.estimate(core)
    if estimate <= 1:
        return [core]

    # pyphen 未切分：短词（如 one/are）优先视为单音节，避免 syllables 误估。
    if len(core) <= 3:
        return [core]

    regex_parts = _ENGLISH_SYLLABLE_PART_RE.findall(core)
    if regex_parts and len(regex_parts) == estimate:
        return regex_parts

    if regex_parts:
        adjusted = _adjust_syllable_parts(regex_parts, estimate)
        if adjusted:
            return adjusted

    return [core]


def _split_linguistic_syllables(text: str) -> list[str]:
    """按词的实际音节数拆分；CJK 按单字，英文按语言学音节。"""
    normalized = _normalize_lyric_text(text)
    if normalized is None:
        return []
    core = normalized.rstrip()
    trailing_space = normalized.endswith(" ")
    if not core:
        return []

    if _is_cjk_text(normalized):
        parts = [char for char in core if char.strip()]
    else:
        parts = _split_english_syllable_parts(core)

    if not parts:
        return []
    if trailing_space:
        parts[-1] = parts[-1] + " "
    return parts


def _part_merged_words(song: SongData, part: str) -> list[MergedLyricWord]:
    if part == "A":
        return song.merged_words_part_a
    if part == "B":
        return song.merged_words_part_b
    raise ValueError(f"未知声部: {part}")


def _lyrics_by_signature_for_part(
    notes: list[Note],
    song: SongData,
    part: str,
    lyric_granularity: LyricGranularity,
) -> dict[NoteSignature, str]:
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    if lyric_granularity == "word":
        index_map = map_merged_word_lyrics_to_notes(
            sorted_notes, _part_merged_words(song, part), part=part
        )
    elif lyric_granularity == "word_syllable":
        groups = [
            group
            for group in _part_merged_words(song, part)
            if _normalize_lyric_text(group.text) is not None
        ]
        index_map = map_merged_word_syllable_lyrics_to_notes(
            sorted_notes, groups, part=part
        )
    else:
        syllable_words = _prepare_syllable_words(_part_syllable_words(song, part))
        index_map = map_lyrics_to_notes(sorted_notes, syllable_words, part=part)
    return {_note_signature(sorted_notes[i]): text for i, text in index_map.items()}


def _note_signature(note: Note) -> NoteSignature:
    return note.start, note.end, note.key


def _midi_pitch(note: Note, *, lower_octave: bool) -> int:
    pitch = note.key - OCTAVE_SEMITONES if lower_octave else note.key
    return max(0, min(127, pitch))


def _lyrics_by_signature(notes: list[Note], words: list[LyricWord]) -> dict[NoteSignature, str]:
    if not words:
        return {}
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    index_map = map_lyrics_to_notes(sorted_notes, words)
    return {_note_signature(sorted_notes[i]): text for i, text in index_map.items()}


def _merge_lyrics_by_signature(
    song: SongData,
    notes_a: list[Note],
    notes_b: list[Note],
    lyric_granularity: LyricGranularity,
    *,
    notes_other: list[Note] | None = None,
) -> dict[NoteSignature, str]:
    """合并轨：首音按 A/B 声部匹配，延音符沿全部音符延伸。"""
    notes_other = notes_other or []
    combined = sorted(
        _dedupe_notes(notes_a + notes_b + notes_other),
        key=lambda n: (n.start, n.end, n.key),
    )
    if lyric_granularity == "word_syllable":
        tagged_groups: list[tuple[str, MergedLyricWord]] = []
        for part in ("A", "B"):
            for group in _part_merged_words(song, part):
                if _normalize_lyric_text(group.text) is None:
                    continue
                tagged_groups.append((part, group))
        index_map = _assign_tagged_merged_syllables_to_note_indices(
            combined, tagged_groups
        )
    else:
        tagged: list[tuple[str, LyricWord]] = []
        for part in ("A", "B"):
            tagged.extend(
                (part, word)
                for word in _collect_words_for_part(song, part, lyric_granularity)
            )
        index_map = _assign_tagged_words_to_note_indices(combined, tagged)
    return {_note_signature(combined[i]): text for i, text in index_map.items()}


def _dedupe_notes(notes: list[Note]) -> list[Note]:
    seen: set[NoteSignature] = set()
    unique: list[Note] = []
    for note in sorted(notes, key=lambda n: (n.start, n.end, n.key)):
        signature = _note_signature(note)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(note)
    return unique


_TIMELINE_MARKER = 0
_TIMELINE_LYRIC = 1
_TIMELINE_NOTE_ON = 2
_TIMELINE_NOTE_OFF = 3

NOTE_VELOCITY = 100


def _new_midi_file() -> mido.MidiFile:
    return mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT, charset="utf-8")


def _build_conductor_track(song: SongData, *, write_tempo: bool) -> mido.MidiTrack:
    """参考 KTV MIDI：独立指挥轨，仅含拍号与速度。"""
    track = mido.MidiTrack()
    track.append(
        mido.MetaMessage(
            "time_signature",
            numerator=4,
            denominator=4,
            clocks_per_click=24,
            notated_32nd_notes_per_beat=8,
            time=0,
        )
    )
    bpm = song.tempo_bpm if write_tempo else DEFAULT_TEMPO_BPM
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def _assemble_midi(
    song: SongData,
    melody_tracks: list[mido.MidiTrack],
    *,
    write_tempo: bool,
) -> mido.MidiFile:
    midi = _new_midi_file()
    midi.tracks.append(_build_conductor_track(song, write_tempo=write_tempo))
    midi.tracks.extend(melody_tracks)
    return midi


def _format_ktv_lyric_text(text: str) -> str:
    """参考 KTV MIDI：CJK 单字、延音为 -。"""
    if text == EXTENSION_SYLLABLE:
        return EXTENSION_SYLLABLE
    stripped = text.strip()
    if not stripped:
        return text
    if _CJK_CHAR_RE.search(stripped):
        for char in stripped:
            if char.strip():
                return char
    return text


def _format_ktv_lyric_map(lyrics: dict[NoteSignature, str]) -> dict[NoteSignature, str]:
    return {signature: _format_ktv_lyric_text(text) for signature, text in lyrics.items()}


def _append_lyric_meta(track: mido.MidiTrack, text: str, delta: int) -> None:
    track.append(mido.MetaMessage("lyrics", text=text, time=delta))


def _format_section_marker(name: str) -> str:
    name = name.strip()
    if not name:
        return "Section"
    return name[0].upper() + name[1:]


def _build_note_marker_timeline(
    notes: list[Note],
    sections: list[SongSection],
    lyrics: dict[NoteSignature, str],
    tick_tempo: int,
    *,
    write_section_markers: bool,
    write_lyrics: bool,
    lower_octave: bool,
) -> list[tuple[int, int, mido.Message | mido.MetaMessage]]:
    """按绝对 tick 收集 marker 与音符事件，供按时间顺序写入同轨。"""
    events: list[tuple[int, int, mido.Message | mido.MetaMessage]] = []

    if write_section_markers:
        for section in sorted(sections, key=lambda item: (item.start, item.seq)):
            start_ticks = _abs_ticks(section.start, tick_tempo, TICKS_PER_BEAT)
            events.append(
                (
                    start_ticks,
                    _TIMELINE_MARKER,
                    mido.MetaMessage(
                        "marker",
                        text=_format_section_marker(section.name),
                        time=0,
                    ),
                )
            )

    for note in sorted(notes, key=lambda n: (n.start, n.end, n.key)):
        pitch = _midi_pitch(note, lower_octave=lower_octave)
        start_ticks = _abs_ticks(note.start, tick_tempo, TICKS_PER_BEAT)
        end_ticks = _abs_ticks(note.end, tick_tempo, TICKS_PER_BEAT)
        lyric = lyrics.get(_note_signature(note)) if write_lyrics else None

        if lyric is not None:
            events.append(
                (
                    start_ticks,
                    _TIMELINE_LYRIC,
                    mido.MetaMessage("lyrics", text=lyric, time=0),
                )
            )
        events.append(
            (
                start_ticks,
                _TIMELINE_NOTE_ON,
                mido.Message("note_on", note=pitch, velocity=NOTE_VELOCITY, time=0),
            )
        )
        events.append(
            (
                end_ticks,
                _TIMELINE_NOTE_OFF,
                mido.Message("note_off", note=pitch, velocity=0, time=0),
            )
        )

    events.sort(key=lambda item: (item[0], item[1]))
    return events


def _append_timeline_to_track(
    track: mido.MidiTrack,
    events: list[tuple[int, int, mido.Message | mido.MetaMessage]],
    *,
    initial_ticks: int = 0,
) -> None:
    current_ticks = initial_ticks
    for abs_ticks, _, message in events:
        message.time = max(abs_ticks - current_ticks, 0)
        track.append(message)
        current_ticks = abs_ticks


def _build_lyrics_only_track(
    notes: list[Note],
    lyrics: dict[NoteSignature, str],
    tick_tempo: int,
    track_name: str = "歌词",
) -> mido.MidiTrack:
    """独立歌词轨：按音符起始时间排列 lyrics 元事件，供编辑器「歌词」面板读取。"""
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    current_ticks = 0

    for note in sorted(notes, key=lambda n: (n.start, n.end, n.key)):
        start_ticks = _abs_ticks(note.start, tick_tempo, TICKS_PER_BEAT)
        lyric = lyrics.get(_note_signature(note))
        if lyric is None:
            current_ticks = max(current_ticks, start_ticks)
            continue
        delta = max(start_ticks - current_ticks, 0)
        _append_lyric_meta(track, lyric, delta)
        current_ticks = start_ticks

    return track


def _append_notes_to_track(
    track: mido.MidiTrack,
    notes: list[Note],
    lyrics: dict[NoteSignature, str],
    tick_tempo: int,
    *,
    write_lyrics: bool,
    lower_octave: bool,
    initial_ticks: int = 0,
) -> None:
    notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    current_ticks = initial_ticks

    for note in notes:
        pitch = _midi_pitch(note, lower_octave=lower_octave)
        start_ticks = _abs_ticks(note.start, tick_tempo, TICKS_PER_BEAT)
        end_ticks = _abs_ticks(note.end, tick_tempo, TICKS_PER_BEAT)
        delta_on = max(start_ticks - current_ticks, 0)
        duration_ticks = max(end_ticks - start_ticks, 1)
        lyric = lyrics.get(_note_signature(note)) if write_lyrics else None

        if lyric is not None:
            _append_lyric_meta(track, lyric, delta_on)
            track.append(
                mido.Message("note_on", note=pitch, velocity=NOTE_VELOCITY, time=0)
            )
            current_ticks = start_ticks
        else:
            track.append(
                mido.Message("note_on", note=pitch, velocity=NOTE_VELOCITY, time=delta_on)
            )
            current_ticks = start_ticks

        track.append(mido.Message("note_off", note=pitch, velocity=0, time=duration_ticks))
        current_ticks = end_ticks


def _build_melody_track(
    notes: list[Note],
    song: SongData,
    part: str,
    tick_tempo: int,
    track_name: str,
    *,
    write_lyrics: bool,
    lyric_granularity: LyricGranularity,
    lower_octave: bool,
    write_section_markers: bool = False,
    lyric_map_override: dict[NoteSignature, str] | None = None,
) -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    lyric_map: dict[NoteSignature, str] = {}
    if lyric_map_override is not None:
        lyric_map = lyric_map_override
    elif write_lyrics and part in ("A", "B"):
        lyric_map = _format_ktv_lyric_map(
            _lyrics_by_signature_for_part(notes, song, part, lyric_granularity)
        )
    elif write_lyrics and part not in ("A", "B"):
        lyric_map = {}
    if write_section_markers and song.sections:
        timeline = _build_note_marker_timeline(
            notes,
            song.sections,
            lyric_map,
            tick_tempo,
            write_section_markers=True,
            write_lyrics=write_lyrics,
            lower_octave=lower_octave,
        )
        _append_timeline_to_track(track, timeline)
    else:
        _append_notes_to_track(
            track,
            notes,
            lyric_map,
            tick_tempo,
            write_lyrics=write_lyrics,
            lower_octave=lower_octave,
        )
    return track


def _output_path(output_dir: str, song: SongData, suffix: str) -> str:
    filename = f"{_sanitize_filename(song.title)}_{song.mr_id}-{suffix}.mid"
    path = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)
    return path


def _save_midi(midi: mido.MidiFile, song: SongData, output_dir: str, suffix: str) -> str:
    output_path = _output_path(output_dir, song, suffix)
    midi.save(output_path)
    return output_path


def _export_single_part(
    song: SongData,
    output_dir: str,
    part: str,
    suffix: str,
    track_name: str,
    *,
    write_tempo: bool,
    write_lyrics: bool,
    lyric_granularity: LyricGranularity,
    lower_octave: bool,
    write_section_markers: bool,
    allow_empty: bool = False,
) -> str:
    notes = filter_notes(song.notes, part)
    if not notes and not allow_empty:
        raise ValueError(f"{track_name} 没有可导出的音符")

    _, tick_tempo = _resolve_tick_tempo(song, write_tempo)
    midi = _assemble_midi(
        song,
        [
            _build_melody_track(
                notes,
                song,
                part,
                tick_tempo,
                track_name,
                write_lyrics=write_lyrics and bool(notes),
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers,
            )
        ],
        write_tempo=write_tempo,
    )
    return _save_midi(midi, song, output_dir, suffix)


def _export_merge_same_track(
    song: SongData,
    output_dir: str,
    *,
    write_tempo: bool,
    write_lyrics: bool,
    lyric_granularity: LyricGranularity,
    lower_octave: bool,
    write_section_markers: bool,
) -> str:
    notes_a = filter_notes(song.notes, "A")
    notes_b = filter_notes(song.notes, "B")
    notes_other = filter_notes(song.notes, "O")
    if not notes_a and not notes_b and not notes_other:
        raise ValueError("没有可导出的音符")

    combined = _dedupe_notes(notes_a + notes_b + notes_other)
    lyric_map = (
        _format_ktv_lyric_map(
            _merge_lyrics_by_signature(
                song, notes_a, notes_b, lyric_granularity, notes_other=notes_other
            )
        )
        if write_lyrics
        else {}
    )

    _, tick_tempo = _resolve_tick_tempo(song, write_tempo)
    melody = mido.MidiTrack()
    melody.append(mido.MetaMessage("track_name", name="合并同轨", time=0))
    if write_section_markers and song.sections:
        timeline = _build_note_marker_timeline(
            combined,
            song.sections,
            lyric_map,
            tick_tempo,
            write_section_markers=True,
            write_lyrics=write_lyrics,
            lower_octave=lower_octave,
        )
        _append_timeline_to_track(melody, timeline)
    else:
        _append_notes_to_track(
            melody,
            combined,
            lyric_map,
            tick_tempo,
            write_lyrics=write_lyrics,
            lower_octave=lower_octave,
        )

    midi = _assemble_midi(song, [melody], write_tempo=write_tempo)
    return _save_midi(midi, song, output_dir, "合并同轨")


def _export_merge_multi_track(
    song: SongData,
    output_dir: str,
    *,
    write_tempo: bool,
    write_lyrics: bool,
    lyric_granularity: LyricGranularity,
    lower_octave: bool,
    write_section_markers: bool,
) -> str:
    notes_a = filter_notes(song.notes, "A")
    notes_b = filter_notes(song.notes, "B")
    notes_other = filter_notes(song.notes, "O")
    if not notes_a and not notes_b and not notes_other:
        raise ValueError("没有可导出的音符")

    _, tick_tempo = _resolve_tick_tempo(song, write_tempo)
    melody_tracks: list[mido.MidiTrack] = []

    part_tracks: list[tuple[list[Note], str, str, bool]] = []
    if notes_a:
        part_tracks.append((notes_a, "A", "A声部", write_lyrics))
    if notes_b:
        part_tracks.append((notes_b, "B", "B声部", write_lyrics))
    if notes_other:
        part_tracks.append((notes_other, "O", "Other", False))

    for index, (notes, part, track_name, part_write_lyrics) in enumerate(part_tracks):
        is_first_melody_track = index == 0
        melody_tracks.append(
            _build_melody_track(
                notes,
                song,
                part,
                tick_tempo,
                track_name,
                write_lyrics=part_write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers and is_first_melody_track,
            )
        )

    midi = _assemble_midi(song, melody_tracks, write_tempo=write_tempo)
    return _save_midi(midi, song, output_dir, "合并分轨")


def _export_lyric_lang_split_tracks(
    song: SongData,
    output_dir: str,
    *,
    write_tempo: bool,
    write_lyrics: bool,
    lyric_granularity: LyricGranularity,
    lower_octave: bool,
    write_section_markers: bool,
) -> str:
    """按原文（ori）脚本分轨：单个 MIDI 内每种语种/数字各占一条旋律轨。"""
    notes_a = filter_notes(song.notes, "A")
    notes_b = filter_notes(song.notes, "B")
    notes_other = filter_notes(song.notes, "O")
    combined = _dedupe_notes(notes_a + notes_b + notes_other)
    if not combined:
        raise ValueError("没有可导出的音符")

    sorted_combined = sorted(combined, key=lambda n: (n.start, n.end, n.key))
    scripts_present = _scripts_present_in_song(song, lyric_granularity)
    if not scripts_present:
        raise ValueError("未找到可分类的原文字词，无法按语种分轨")

    _, tick_tempo = _resolve_tick_tempo(song, write_tempo)
    melody_tracks: list[mido.MidiTrack] = []
    track_index = 0
    for script in scripts_present:
        script_notes, script_lyric_map = _script_track_lyrics_and_notes(
            song, sorted_combined, script, lyric_granularity
        )
        if not script_notes:
            continue

        track_label = LYRIC_SCRIPT_TRACK_LABELS[script]
        melody_tracks.append(
            _build_melody_track(
                script_notes,
                song,
                "merge",
                tick_tempo,
                track_label,
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers and track_index == 0,
                lyric_map_override=script_lyric_map if write_lyrics else {},
            )
        )
        track_index += 1

    if not melody_tracks:
        raise ValueError("没有可导出的语种类别")

    midi = _assemble_midi(song, melody_tracks, write_tempo=write_tempo)
    return _save_midi(midi, song, output_dir, "歌词语种分轨")


def _prepare_song_for_export(
    song: SongData,
    part_mode: PartMode,
    *,
    exclude_rap_sections: bool,
    remove_non_melody_notes: bool,
    time_offset_ms: int,
    audio_reference_calibration: bool,
) -> tuple[SongData, "AudioCalibrationResult | None", str | None]:
    from core.audio_calibration import AudioCalibrationResult, compute_audio_calibration_offset

    if part_mode == "lyric_lang_split" and song.source_path:
        song = load_song_json(song.source_path, "ori")
    if exclude_rap_sections:
        song = exclude_rap_sections_from_song(song)
    if remove_non_melody_notes:
        song = exclude_non_melody_notes_from_song(song)

    calibration: AudioCalibrationResult | None = None
    calibration_error: str | None = None
    total_offset_ms = time_offset_ms
    if audio_reference_calibration:
        try:
            calibration = compute_audio_calibration_offset(song)
            total_offset_ms += calibration.offset_ms
        except Exception as exc:
            calibration_error = str(exc)

    song = apply_song_time_offset(song, total_offset_ms)
    return song, calibration, calibration_error


def export_song(
    song: SongData,
    output_dir: str,
    part_mode: PartMode,
    *,
    write_tempo: bool = True,
    write_lyrics: bool = True,
    lyric_granularity: LyricGranularity = "word",
    lower_octave: bool = True,
    write_section_markers: bool = False,
    exclude_rap_sections: bool = False,
    remove_non_melody_notes: bool = False,
    time_offset_ms: int = 0,
    audio_reference_calibration: bool = True,
    calibration_log: list[str] | None = None,
) -> list[str]:
    song, calibration, calibration_error = _prepare_song_for_export(
        song,
        part_mode,
        exclude_rap_sections=exclude_rap_sections,
        remove_non_melody_notes=remove_non_melody_notes,
        time_offset_ms=time_offset_ms,
        audio_reference_calibration=audio_reference_calibration,
    )
    if calibration_log is not None and audio_reference_calibration:
        if calibration is not None:
            calibration_log.append(
                f"音频校准 {calibration.offset_ms:+d} ms "
                f"(匹配 MIDI {calibration.matched_midi_ms} ms ↔ "
                f"音频 {calibration.matched_audio_ms} ms, "
                f"命中 {calibration.match_count} 个音符)"
            )
        else:
            calibration_log.append(
                f"音频校准跳过（{calibration_error or '未知原因'}）"
            )
    if part_mode == "separate":
        if not song.notes:
            raise ValueError("没有可导出的音符")

        separate_parts: list[tuple[str, str, str, bool]] = [
            ("A", "A声部", "A声部", write_lyrics),
            ("B", "B声部", "B声部", write_lyrics),
            ("O", "Other", "Other", False),
        ]
        return [
            _export_single_part(
                song,
                output_dir,
                part,
                suffix,
                track_name,
                write_tempo=write_tempo,
                write_lyrics=part_write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers,
                allow_empty=True,
            )
            for part, suffix, track_name, part_write_lyrics in separate_parts
        ]

    if part_mode == "male_only":
        return [
            _export_single_part(
                song,
                output_dir,
                "A",
                "A声部",
                "A声部",
                write_tempo=write_tempo,
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers,
            )
        ]

    if part_mode == "female_only":
        return [
            _export_single_part(
                song,
                output_dir,
                "B",
                "B声部",
                "B声部",
                write_tempo=write_tempo,
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers,
            )
        ]

    if part_mode == "merge_same":
        return [
            _export_merge_same_track(
                song,
                output_dir,
                write_tempo=write_tempo,
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers,
            )
        ]

    if part_mode == "merge_multi":
        return [
            _export_merge_multi_track(
                song,
                output_dir,
                write_tempo=write_tempo,
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers,
            )
        ]

    if part_mode == "lyric_lang_split":
        return [
            _export_lyric_lang_split_tracks(
                song,
                output_dir,
                write_tempo=write_tempo,
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
                write_section_markers=write_section_markers,
            )
        ]

    raise ValueError(f"未知导出模式: {part_mode}")


def midi_duration_seconds(midi_path: str, fallback_tempo_bpm: float = DEFAULT_TEMPO_BPM) -> float:
    midi = mido.MidiFile(midi_path)
    tempo = mido.bpm2tempo(fallback_tempo_bpm)
    total_ticks = 0.0

    for track in midi.tracks:
        track_ticks = 0
        for msg in track:
            track_ticks += msg.time
            if msg.type == "set_tempo":
                tempo = msg.tempo
        total_ticks = max(total_ticks, track_ticks)

    return mido.tick2second(total_ticks, midi.ticks_per_beat, tempo)
