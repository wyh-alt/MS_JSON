import os
import re
import unicodedata
from typing import Literal

import mido

from core.parser import LyricWord, MergedLyricWord, Note, SongData

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
]
LyricGranularity = Literal["syllable", "word"]

PART_MODE_LABELS: list[tuple[str, PartMode]] = [
    ("男女声部合并导出（同轨）", "merge_same"),
    ("男女声部合并导出（分轨）", "merge_multi"),
    ("男女声部分别导出", "separate"),
    ("仅男声部", "male_only"),
    ("仅女声部", "female_only"),
]

LYRIC_GRANULARITY_LABELS: list[tuple[str, LyricGranularity]] = [
    ("单词级（英文整词，后续 -）", "word"),
    ("与 JSON 条目一致", "syllable"),
]

_CJK_CHAR_RE = re.compile(r"[가-힣一-龥ぁ-んァ-ン]")


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


def _match_word_to_notes(word: LyricWord, sorted_notes: list[Note]) -> list[Note] | None:
    """一词优先对应一个音符；仅当词时长明显超出单音覆盖时才使用连续音符链。"""
    ranked = _ranked_notes_for_word(word, sorted_notes)
    if not ranked:
        return None

    best = ranked[0]
    word_duration = max(1, word.end - word.start)
    coverage = _word_note_overlap(word, best) / word_duration
    if word.end <= best.end + TIME_TOLERANCE_MS or coverage >= 0.6:
        return [best]

    note_index = {id(n): i for i, n in enumerate(sorted_notes)}
    chain = [best]
    for next_note in sorted_notes[note_index[id(best)] + 1 :]:
        if next_note.start - chain[-1].end > TIME_TOLERANCE_MS:
            break
        chain.append(next_note)
        chain_coverage = (
            sum(_word_note_overlap(word, note) for note in chain) / word_duration
        )
        if (
            chain[-1].end >= word.end - TIME_TOLERANCE_MS
            and chain_coverage >= 0.6
        ):
            return chain

    return [best]


_APOSTROPHES = frozenset("'’‘ʼ")
# ~ 等装饰符号在 Unicode 中常为 Sm，需显式列入
_EXPLICIT_LYRIC_PUNCT = frozenset(
    "~～!！?？…·•@#$%^&*()（）[]{}|\\/<>`+=，,。.．；;：:「」『』【】_—－-"
)


def _strip_lyric_punctuation(text: str) -> str:
    """去除歌词标点（如 ~ ！），保留撇号用于缩写；不处理延音标记 -。"""
    chars: list[str] = []
    for ch in text:
        if ch in _APOSTROPHES:
            chars.append(ch)
        elif ch in _EXPLICIT_LYRIC_PUNCT or unicodedata.category(ch).startswith("P"):
            continue
        else:
            chars.append(ch)
    return re.sub(r" +", " ", "".join(chars)).strip()


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


def map_lyrics_to_notes(notes: list[Note], words: list[LyricWord]) -> dict[int, str]:
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    note_index = {id(n): i for i, n in enumerate(sorted_notes)}
    lyric_map: dict[int, str] = {}

    for word in words:
        matched = _match_word_to_notes(word, sorted_notes)
        if not matched:
            continue

        first_idx = note_index[id(matched[0])]
        if len(matched) == 1:
            if first_idx not in lyric_map:
                lyric_map[first_idx] = word.text
            elif lyric_map[first_idx] == EXTENSION_SYLLABLE:
                lyric_map[first_idx] = word.text
            elif _is_lyric_fragment(word.text):
                lyric_map[first_idx] += word.text
            else:
                continue
            continue

        if first_idx in lyric_map and lyric_map[first_idx] != EXTENSION_SYLLABLE:
            continue
        lyric_map[first_idx] = word.text
        for note in matched[1:]:
            lyric_map[note_index[id(note)]] = EXTENSION_SYLLABLE

    return _refine_lyric_index_map(lyric_map)


def map_merged_word_lyrics_to_notes(
    notes: list[Note], word_groups: list[MergedLyricWord]
) -> dict[int, str]:
    """单词级：整词写在首音，同一词跨多音时后续音符写 -。"""
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    note_index = {id(n): i for i, n in enumerate(sorted_notes)}
    lyric_map: dict[int, str] = {}

    for group in word_groups:
        note_indices: list[int] = []
        seen: set[int] = set()

        for syllable in group.syllables:
            matched = _match_word_to_notes(syllable, sorted_notes)
            if not matched:
                continue
            for note in matched:
                idx = note_index[id(note)]
                if idx in seen:
                    continue
                seen.add(idx)
                note_indices.append(idx)

        if not note_indices:
            continue

        note_indices.sort(key=lambda i: sorted_notes[i].start)
        text = _normalize_lyric_text(group.text)
        if text is None:
            continue

        first_idx = note_indices[0]
        if first_idx in lyric_map:
            if lyric_map[first_idx] == EXTENSION_SYLLABLE:
                lyric_map[first_idx] = text
            else:
                continue
        else:
            lyric_map[first_idx] = text

        for idx in note_indices[1:]:
            if idx not in lyric_map:
                lyric_map[idx] = EXTENSION_SYLLABLE

    return lyric_map


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
    syllable_words = _prepare_syllable_words(_part_syllable_words(song, part))
    if lyric_granularity == "word":
        index_map = map_merged_word_lyrics_to_notes(
            sorted_notes, _part_merged_words(song, part)
        )
    else:
        index_map = map_lyrics_to_notes(sorted_notes, syllable_words)
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
) -> dict[NoteSignature, str]:
    merged: dict[NoteSignature, str] = {}
    for part, notes in (("A", notes_a), ("B", notes_b)):
        for signature, text in _lyrics_by_signature_for_part(
            notes, song, part, lyric_granularity
        ).items():
            merged.setdefault(signature, text)
    return merged


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


def _append_lyric_meta(track: mido.MidiTrack, text: str, delta: int) -> None:
    track.append(mido.MetaMessage("lyrics", text=text, time=delta))


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
        track.append(mido.MetaMessage("lyrics", text=lyric, time=delta))
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
) -> None:
    notes = sorted(notes, key=lambda n: (n.start, n.end, n.key))
    current_ticks = 0

    for note in notes:
        pitch = _midi_pitch(note, lower_octave=lower_octave)
        start_ticks = _abs_ticks(note.start, tick_tempo, TICKS_PER_BEAT)
        end_ticks = _abs_ticks(note.end, tick_tempo, TICKS_PER_BEAT)
        delta_on = max(start_ticks - current_ticks, 0)
        duration_ticks = max(end_ticks - start_ticks, 1)
        lyric = lyrics.get(_note_signature(note)) if write_lyrics else None

        if lyric is not None:
            _append_lyric_meta(track, lyric, delta_on)
            track.append(mido.Message("note_on", note=pitch, velocity=80, time=0))
            current_ticks = start_ticks
        else:
            track.append(mido.Message("note_on", note=pitch, velocity=80, time=delta_on))
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
    include_tempo: bool,
    write_tempo: bool,
    lower_octave: bool,
) -> mido.MidiTrack:
    track = mido.MidiTrack()
    if include_tempo and write_tempo:
        track.append(
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(song.tempo_bpm), time=0)
        )
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    lyric_map = (
        _lyrics_by_signature_for_part(notes, song, part, lyric_granularity)
        if write_lyrics
        else {}
    )
    _append_notes_to_track(
        track, notes, lyric_map, tick_tempo, write_lyrics=write_lyrics, lower_octave=lower_octave
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
) -> str:
    notes = filter_notes(song.notes, part)
    if not notes:
        raise ValueError(f"{track_name} 没有可导出的音符")

    _, tick_tempo = _resolve_tick_tempo(song, write_tempo)
    midi = mido.MidiFile(type=0, ticks_per_beat=TICKS_PER_BEAT, charset="utf-8")
    midi.tracks.append(
        _build_melody_track(
            notes,
            song,
            part,
            tick_tempo,
            track_name,
            write_lyrics=write_lyrics,
            lyric_granularity=lyric_granularity,
            include_tempo=True,
            write_tempo=write_tempo,
            lower_octave=lower_octave,
        )
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
) -> str:
    notes_a = filter_notes(song.notes, "A")
    notes_b = filter_notes(song.notes, "B")
    if not notes_a and not notes_b:
        raise ValueError("没有可导出的音符")

    combined = _dedupe_notes(notes_a + notes_b)
    lyric_map = (
        _merge_lyrics_by_signature(song, notes_a, notes_b, lyric_granularity)
        if write_lyrics
        else {}
    )

    _, tick_tempo = _resolve_tick_tempo(song, write_tempo)
    melody = mido.MidiTrack()
    if write_tempo:
        melody.append(
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(song.tempo_bpm), time=0)
        )
    melody.append(mido.MetaMessage("track_name", name="合并同轨", time=0))
    _append_notes_to_track(
        melody,
        combined,
        lyric_map,
        tick_tempo,
        write_lyrics=write_lyrics,
        lower_octave=lower_octave,
    )

    if write_lyrics and lyric_map:
        midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT, charset="utf-8")
        midi.tracks.append(_build_lyrics_only_track(combined, lyric_map, tick_tempo))
        midi.tracks.append(melody)
    else:
        midi = mido.MidiFile(type=0, ticks_per_beat=TICKS_PER_BEAT, charset="utf-8")
        midi.tracks.append(melody)
    return _save_midi(midi, song, output_dir, "合并同轨")


def _export_merge_multi_track(
    song: SongData,
    output_dir: str,
    *,
    write_tempo: bool,
    write_lyrics: bool,
    lyric_granularity: LyricGranularity,
    lower_octave: bool,
) -> str:
    notes_a = filter_notes(song.notes, "A")
    notes_b = filter_notes(song.notes, "B")
    if not notes_a and not notes_b:
        raise ValueError("没有可导出的音符")

    _, tick_tempo = _resolve_tick_tempo(song, write_tempo)
    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT, charset="utf-8")

    conductor = mido.MidiTrack()
    if write_tempo:
        conductor.append(
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(song.tempo_bpm), time=0)
        )
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    midi.tracks.append(conductor)

    if notes_a:
        midi.tracks.append(
            _build_melody_track(
                notes_a,
                song,
                "A",
                tick_tempo,
                "男声部",
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                include_tempo=False,
                write_tempo=write_tempo,
                lower_octave=lower_octave,
            )
        )
    if notes_b:
        midi.tracks.append(
            _build_melody_track(
                notes_b,
                song,
                "B",
                tick_tempo,
                "女声部",
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                include_tempo=False,
                write_tempo=write_tempo,
                lower_octave=lower_octave,
            )
        )

    return _save_midi(midi, song, output_dir, "合并分轨")


def export_song(
    song: SongData,
    output_dir: str,
    part_mode: PartMode,
    *,
    write_tempo: bool = True,
    write_lyrics: bool = True,
    lyric_granularity: LyricGranularity = "word",
    lower_octave: bool = True,
) -> list[str]:
    if part_mode == "separate":
        exported: list[str] = []
        if filter_notes(song.notes, "A"):
            exported.append(
                _export_single_part(
                    song,
                    output_dir,
                    "A",
                    "男声部",
                    "男声部",
                    write_tempo=write_tempo,
                    write_lyrics=write_lyrics,
                    lyric_granularity=lyric_granularity,
                    lower_octave=lower_octave,
                )
            )
        if filter_notes(song.notes, "B"):
            exported.append(
                _export_single_part(
                    song,
                    output_dir,
                    "B",
                    "女声部",
                    "女声部",
                    write_tempo=write_tempo,
                    write_lyrics=write_lyrics,
                    lyric_granularity=lyric_granularity,
                    lower_octave=lower_octave,
                )
            )
        if not exported:
            raise ValueError("没有可导出的音符")
        return exported

    if part_mode == "male_only":
        return [
            _export_single_part(
                song,
                output_dir,
                "A",
                "男声部",
                "男声部",
                write_tempo=write_tempo,
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
            )
        ]

    if part_mode == "female_only":
        return [
            _export_single_part(
                song,
                output_dir,
                "B",
                "女声部",
                "女声部",
                write_tempo=write_tempo,
                write_lyrics=write_lyrics,
                lyric_granularity=lyric_granularity,
                lower_octave=lower_octave,
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
