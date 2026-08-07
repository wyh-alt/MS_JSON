"""验证歌词处理与段落信息处理在共同歌词内容上的时间点是否吻合。

检查内容：
1. 原始 JSON：section.start/end 与段内 line/word 时间戳的关系
   - line.start/end 是否等于首/末 word 的 start/end
   - 所有 line 是否完全落在 section 区间内
   - 段内首行歌词 start 是否等于 section.start（段落起止时间是否从歌词得到）
2. 处理后（导出路径）：
   - 歌词导出行（render_lyrics CSV）vs 段落表格行（collect_section_export_rows）
   - 段落起止时间 vs 段内导出歌词行的起止时间
3. 带时间偏移时（apply_song_time_offset vs _section_export_times）是否仍吻合
"""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.lyric_exporter import collect_section_export_rows, render_lyrics  # noqa: E402
from core.parser import collect_json_files, load_song_json  # noqa: E402

MS = "ms"

def _parse_ksc_time(text: str) -> int:
    """mm:ss.fff -> 毫秒（与 _format_ksc_time 互逆）。"""
    minutes, rest = text.split(":", 1)
    seconds = float(rest)
    return int(minutes) * 60_000 + round(seconds * 1000)


def _render_lines(song, offset_ms: int) -> list[tuple[int, int, str]]:
    """歌词导出路径（CSV）解析出的行时间 (start, end, text)。

    与 export_song_lyrics 一致：先整体偏移再渲染。
    """
    if offset_ms:
        from core.parser import apply_song_time_offset

        song = apply_song_time_offset(song, offset_ms)
    content = render_lyrics(
        song,
        lyric_format="csv",
        part="all",
        title_lang="origin",
        artist_lang="origin",
    )
    reader = csv.reader(io.StringIO(content))
    next(reader)  # header
    rows = []
    for row in reader:
        if not row:
            continue
        start, end, text = int(row[0]), int(row[1]), row[2]
        rows.append((start, end, text))
    return rows


def _section_rows(song, offset_ms: int) -> list[tuple]:
    """段落表格导出路径的行：(name, start_ms, end_ms, lyric_lines)。"""
    rows = collect_section_export_rows(
        song,
        title_lang="origin",
        artist_lang="origin",
        time_offset_ms=offset_ms,
        audio_reference_calibration=False,
    )
    parsed = []
    for row in rows:
        name = row[3]
        start_ms = _parse_ksc_time(row[4])
        end_ms = _parse_ksc_time(row[5])
        lyric_lines = [ln for ln in row[6].split("\n") if ln.strip()]
        parsed.append((name, start_ms, end_ms, lyric_lines))
    return parsed


def _check_raw_json(path: str, problems: list[str], checked: dict) -> None:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    mnote = data.get("mnote") or data.get("msi_melody_note", {})
    sections = mnote.get("section", [])

    for section in sections:
        name = str(section.get("name", "") or "")
        if not name:
            continue
        sec_start = int(section.get("start", 0))
        sec_end = int(section.get("end", 0))
        lines = section.get("line", [])
        lyric_lines = [
            ln for ln in lines if any(
                str(word.get("ori", "") or "").strip() for word in ln.get("word", [])
            )
        ]
        checked["sections_raw"] += 1
        if not lyric_lines:
            checked["sections_no_lyric"] += 1
            continue

        first_line = lyric_lines[0]
        last_line = lyric_lines[-1]
        fl_start = int(first_line.get("start", 0))
        ll_end = int(last_line.get("end", 0))

        # 1) 段落起始时间是否等于段内首行歌词起始时间
        if fl_start != sec_start:
            problems.append(
                f"[{Path(path).stem}] {name}: section.start={sec_start} "
                f"!= 首行歌词 line.start={fl_start} (首行: {first_line.get('ori', '')[:20]!r})"
            )

        # 2) line.start/end 是否等于首/末 word 的 start/end
        for ln in lyric_lines:
            words = ln.get("word", [])
            lyric_words = [w for w in words if str(w.get("ori", "") or "").strip()]
            if not lyric_words:
                continue
            ln_start = int(ln.get("start", 0))
            ln_end = int(ln.get("end", 0))
            w_start = int(lyric_words[0]["start"])
            w_end = int(lyric_words[-1]["end"])
            if ln_start != w_start:
                checked["line_start_mismatch"] += 1
                problems.append(
                    f"[{Path(path).stem}] {name}: line.start={ln_start} "
                    f"!= 首word.start={w_start} (line: {ln.get('ori', '')[:20]!r})"
                )
            if ln_end != w_end:
                checked["line_end_mismatch"] += 1
                problems.append(
                    f"[{Path(path).stem}] {name}: line.end={ln_end} "
                    f"!= 末word.end={w_end} (line: {ln.get('ori', '')[:20]!r})"
                )

        # 3) 所有 line 是否完全落在 section 区间内
        for ln in lyric_lines:
            ln_start = int(ln.get("start", 0))
            ln_end = int(ln.get("end", 0))
            if ln_start < sec_start or ln_end > sec_end:
                checked["line_out_of_section"] += 1
                problems.append(
                    f"[{Path(path).stem}] {name}: line [{ln_start},{ln_end}] "
                    f"超出 section [{sec_start},{sec_end}] "
                    f"({ln.get('ori', '')[:20]!r})"
                )

        # 4) 段落结束时间 vs 末行歌词结束时间
        if ll_end > sec_end:
            checked["last_line_after_section_end"] += 1
            problems.append(
                f"[{Path(path).stem}] {name}: 末行歌词 end={ll_end} > section.end={sec_end}"
            )


def _check_export(path: str, problems: list[str], checked: dict) -> None:
    """处理后对比：段落表格行 vs 歌词导出行（共同歌词内容的时间点）。"""
    song = load_song_json(path, "ori")
    stem = Path(path).stem
    sec_rows = _section_rows(song, 0)
    lyric_lines = _render_lines(song, 0)
    lyric_by_text: dict[str, list[tuple[int, int, str]]] = {}
    for start, end, text in lyric_lines:
        lyric_by_text.setdefault(text, []).append((start, end, text))

    for name, sec_start, sec_end, sec_texts in sec_rows:
        checked["sections_export"] += 1
        if not sec_texts:
            continue

        # 段落内每行歌词文本，在歌词导出中的时间
        in_section = []
        missing = []
        for text in sec_texts:
            matched = [
                item for item in lyric_by_text.get(text, [])
                if item[0] >= sec_start and item[1] <= sec_end
            ]
            if matched:
                in_section.append((text, matched))
            else:
                missing.append(text)
                checked["text_not_found_in_section"] += 1

        # 歌词导出中与段落区间重叠的行
        overlap = [
            (s, e, t) for s, e, t in lyric_lines
            if s < sec_end and e > sec_start
        ]
        for s, e, t in overlap:
            if s < sec_start or e > sec_end:
                checked["export_line_cross_section"] += 1
                problems.append(
                    f"[{stem}] {name}: 导出行 [{s},{e}] {t[:20]!r} "
                    f"跨越段落边界 [{sec_start},{sec_end}]"
                )

        if not in_section:
            continue

        first_in = min(in_section, key=lambda item: min(x[0] for x in item[1]))
        first_start = min(x[0] for x in first_in[1])
        # 段落起始 vs 段内首行歌词起始
        if first_start != sec_start:
            checked["section_start_vs_first_line"] += 1
            problems.append(
                f"[{stem}] {name}: 段落起始={sec_start} "
                f"!= 段内首行歌词起始={first_start} ({first_in[0][:20]!r})"
            )
        # 段内最后一行歌词结束 vs 段落结束
        last_in = max(in_section, key=lambda item: max(x[1] for x in item[1]))
        last_end = max(x[1] for x in last_in[1])
        if last_end > sec_end:
            checked["section_end_before_last_line"] += 1
            problems.append(
                f"[{stem}] {name}: 段落结束={sec_end} "
                f"< 段内末行歌词结束={last_end} ({last_in[0][:20]!r})"
            )

        if missing:
            problems.append(
                f"[{stem}] {name}: {len(missing)} 行歌词文本在导出中找不到"
                f" 落在段落区间内的行: {missing[0][:20]!r} ..."
            )


def _check_offset(path: str, problems: list[str], checked: dict, offset_ms: int) -> None:
    """带偏移时：歌词行整体偏移 vs 段落 _section_export_times 偏移是否仍吻合。"""
    song = load_song_json(path, "ori")
    stem = Path(path).stem
    sec_rows = _section_rows(song, offset_ms)
    lyric_lines = _render_lines(song, offset_ms)
    lyric_by_text: dict[str, list[tuple[int, int, str]]] = {}
    for start, end, text in lyric_lines:
        lyric_by_text.setdefault(text, []).append((start, end, text))

    for name, sec_start, sec_end, sec_texts in sec_rows:
        if not sec_texts:
            continue
        in_section = []
        for text in sec_texts:
            matched = [
                item for item in lyric_by_text.get(text, [])
                if item[0] >= sec_start and item[1] <= sec_end
            ]
            if matched:
                in_section.append((text, matched))
        if not in_section:
            checked["offset_section_no_lines"] += 1
            problems.append(
                f"[{stem}] {name}: 偏移{offset_ms:+d}后段落 [{sec_start},{sec_end}] "
                f"内无任何导出歌词行"
            )
            continue
        first_in = min(in_section, key=lambda item: min(x[0] for x in item[1]))
        first_start = min(x[0] for x in first_in[1])
        if first_start != sec_start:
            checked["offset_section_start_mismatch"] += 1
            problems.append(
                f"[{stem}] {name}: 偏移{offset_ms:+d}后 段落起始={sec_start} "
                f"!= 段内首行歌词起始={first_start} ({first_in[0][:20]!r})"
            )
        last_in = max(in_section, key=lambda item: max(x[1] for x in item[1]))
        last_end = max(x[1] for x in last_in[1])
        if last_end > sec_end:
            checked["offset_line_out_of_section"] += 1
            problems.append(
                f"[{stem}] {name}: 偏移{offset_ms:+d}后 段内末行歌词 end={last_end} "
                f"> 段落结束={sec_end}"
            )


def main() -> None:
    json_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "MS JSON"
    files = collect_json_files(str(json_dir), valid_only=True)
    if not files:
        print("未找到有效 JSON 文件")
        return

    problems: list[str] = []
    checked = {
        "sections_raw": 0,
        "sections_export": 0,
        "sections_no_lyric": 0,
        "line_start_mismatch": 0,
        "line_end_mismatch": 0,
        "line_out_of_section": 0,
        "last_line_after_section_end": 0,
        "text_not_found_in_section": 0,
        "export_line_cross_section": 0,
        "section_start_vs_first_line": 0,
        "section_end_before_last_line": 0,
        "offset_section_no_lines": 0,
        "offset_section_start_mismatch": 0,
        "offset_line_out_of_section": 0,
    }

    for path in files:
        _check_raw_json(path, problems, checked)
        _check_export(path, problems, checked)
        for offset_ms in (2500, -1500):
            _check_offset(path, problems, checked, offset_ms)

    print(f"检查文件数: {len(files)}")
    print(f"原始 JSON 段落数: {checked['sections_raw']} | 导出段落数: {checked['sections_export']}")
    print(f"无歌词段落(器乐段): {checked['sections_no_lyric']}")
    print()
    print("=== 汇总（各项不一致计数） ===")
    for key in ("line_start_mismatch", "line_end_mismatch", "line_out_of_section",
                "last_line_after_section_end", "section_start_vs_first_line",
                "section_end_before_last_line", "text_not_found_in_section",
                "export_line_cross_section", "offset_section_no_lines",
                "offset_section_start_mismatch", "offset_line_out_of_section"):
        print(f"  {key}: {checked[key]}")
    print()
    if problems:
        print("=== 不一致明细 ===")
        for p in problems:
            print("  -", p)
    else:
        print("=== 结论: 全部吻合，无任何不一致 ===")


if __name__ == "__main__":
    main()
