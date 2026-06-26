# MS JSON 导出工具

从 KTV 打分系统（Sing-It / MS）导出的 JSON 文件中，批量提取旋律、歌词、段落、元数据与音频资源，并导出为 MIDI、歌词文件、Excel 或混音音频。提供基于 PyQt6 + Fluent Widgets 的图形界面。

## 环境要求

- Windows 10 及以上
- Python 3.10+（源码运行或自行打包时）
- 系统 PATH 中可用的 **ffmpeg**（音频下载、混音、转码及音频校准均依赖）

## 安装与运行

```bash
pip install -r requirements.txt
python main.py
```

打包为单文件可执行程序（构建时会将当前环境的 ffmpeg 一并打入）：

```bash
build.bat
```

产物位于 `dist/MS_json.exe`。

## 输入文件

支持拖入或选择单个 JSON 文件、文件夹（递归扫描）。仅处理包含 `mnote.note` 与 `mnote.section` 的有效 MS JSON。

### JSON 数据结构概览

| 层级 | 主要字段 | 说明 |
|------|----------|------|
| 根级 | `mr_id` | 伴奏 ID |
| | `title_origin` / `title_ko` / `title_en` | 曲名（多语言） |
| | `artist_names_*` | 歌手名 |
| | `album_name_*` | 专辑名 |
| | `album_cover_path` | 专辑封面 URL |
| | `original_key` | 原曲调性（`m` 男调 / `w` 女调），用于选择默认旋律轨 |
| | `file_mr_mel_m` / `file_mr_mel_w` | 男调 / 女调旋律音频 |
| | `file_mr_har_m` / `file_mr_har_w` | 男调 / 女调伴奏音频 |
| | `file_mr_drum` / `file_mr_drum_m` / `file_mr_drum_w` | 鼓轨 |
| `mnote` | `note[]` | 旋律音符：`start` / `end`（毫秒）、`key`（MIDI 音高）、`isPartA` / `isPartB` |
| | `section[]` | 段落结构、歌词行与词级时间 |
| | `tempos[]` | 分段速度：`tempo`（BPM）、`end`（该段结束的毫秒时刻） |
| | `existsRom` / `rom_translate_version` | 罗马音相关元数据 |

### 时间轴与速度的关系

**音符、歌词、段落的 `start` / `end` 均为从曲目开头计时的绝对毫秒数（wall-clock），与 BPM 无关。** 它们直接对应音频时间轴上的发生时刻。

`tempos` 是独立的元数据，描述「某段时间内一拍有多快」，主要用于 MIDI 导出时的 tick 换算与指挥轨速度标记，**不会改写** JSON 里原有的毫秒时间。

## 应用功能

主窗口包含四个页面（侧边栏自上而下）：

| 页面 | 功能 |
|------|------|
| 音频下载 | 按 JSON 直链下载 MR 资源，ffmpeg 混音 / 转码 |
| 元数据提取 | 汇总曲目信息 Excel，并按类型下载封面、旋律、伴奏、鼓轨 |
| 歌词导出 | 导出 KSC / LRC / TXT / CSV 歌词及段落信息 Excel |
| MIDI 导出 | 导出 KTV 风格 Type 1 MIDI |

各批量任务页均带有进度条（`BatchProgressPanel`），在后台线程中处理，避免界面卡顿。

---

## 核心模块

### `core/parser.py` — JSON 解析与歌曲模型

**职责**：读取 MS JSON，构建统一的 `SongData` 内存模型，供各导出模块复用。

**主要数据结构**

- `Note`：旋律音符（毫秒起止、音高、A/B 声部标记）
- `LyricWord` / `MergedLyricWord` / `LyricLine`：词级与行级歌词
- `SongSection`：段落（名称、起止、seq、partA/partB）
- `TempoSegment`：速度分段（BPM + 段末毫秒）
- `SongData`：单首曲目的完整解析结果

**解析逻辑**

1. `load_song_json()` 校验 `mnote` 结构后，分别解析音符、段落、歌词、速度、元数据字段。
2. 歌词从 `section[].line[].word[]` 按 `partA` / `partB` 拆分为两套声部；支持 `ori` / `ko` / `rom` / `en` 字段。
3. CJK 文本在合并词组、拆音节时有专门处理；英文词可借助 `pyphen` / `syllables` 做音节划分。
4. `parse_tempo_segments()` 将 `tempos` 转为按 `end_ms` 递增的分段表；`tempo_bpm`（主曲速）取**持续时间最长**的一段。
5. `apply_song_time_offset()` 对音符、歌词、段落、速度分段边界做整体毫秒平移（导出前统一应用）。

**过滤与清洗**

- `exclude_rap_sections_from_song()`：移除 Rap 段落内的音符与歌词。
- `exclude_non_melody_notes_from_song()`：按规则剔除疑似非旋律音符（同音高占位、演唱音域外等），并同步过滤对应歌词。

**工具函数**

- `collect_json_files()` / `scan_json_files()`：扫描路径下 JSON。
- `resolve_title()` / `resolve_artist()` / `resolve_album()`：按语言选取元数据文本。

---

### `core/midi_exporter.py` — MIDI 导出

**职责**：将 `SongData` 转为 KTV 常用 **Type 1 MIDI**（Track 0 指挥轨 + 旋律轨）。

**声部模式**

| 模式 | 输出 |
|------|------|
| 合并导出（同轨） | A + B + Other 合并为一条旋律轨 |
| 合并导出（分轨） | A、B、Other 各占一轨（无 Other 音符时不建轨） |
| 分别导出 | 固定生成 `A声部.mid`、`B声部.mid`、`Other.mid` |
| 歌词语种分轨 | 按汉字 / 韩文 / 英文 / 假名 / 数字等拆成多条旋律轨 |
| 仅 A 声部 / 仅 B 声部 | 只导出对应声部 |

声部判定：`isPartA` → A，`isPartB` → B，双 false → Other。

**时间 → MIDI tick 换算**

JSON 毫秒时间在导出时才参与换算，核心函数为 `_ms_to_export_ticks()`：

- **勾选「写入速度信息」**：按完整 `tempo_segments` 分段换算 tick；指挥轨写入多条 `set_tempo`（完整 tempo map）。
- **不勾选**：指挥轨固定 **120 BPM**；音符 tick 亦按 120 BPM 从毫秒反推，保证回放时刻与 JSON 一致（wall-clock 不变）。

**歌词写入**

- 通过重叠时间、起止接近度等启发式，将词对齐到音符（`map_lyrics_to_notes` 等）。
- 支持单词级、音节级、与 JSON 条目一致三种粒度；CJK 按字、英文延音写 `-`。
- Other 轨无独立歌词；合并同轨时可能从相邻 A/B 歌词延伸 `-`。

**其他选项**

- 音符降低八度（默认开启）
- 段落 marker 写入第一条旋律轨
- 删除 Rap / 非旋律音符（调用 parser 过滤）
- 整体偏移 + 音频参考校准（见下）

**导出流程**

`export_song()` → `_prepare_song_for_export()`（过滤、计算总偏移）→ 按声部模式组装轨 → `_assemble_midi()` → 保存为 `{曲名}_{mr_id}-{后缀}.mid`。

---

### `core/lyric_exporter.py` — 歌词与段落导出

**职责**：将歌词渲染为多种文本格式，并汇总段落信息 Excel。

**歌词格式**

| 格式 | 扩展名 |
|------|--------|
| KSC 小灰熊 | `.txt` |
| KSC | `.ksc` |
| TXT 分句 | `.txt` |
| LRC | `.lrc` |
| CSV | `.csv` |

**逻辑要点**

- 从 `SongData.lines_part_a` / `lines_part_b` 收集行；「全部声部」时合并并按时间去重（合唱段 A/B 各有一份相同歌词）。
- KSC 支持字符 / 单词中括号选项；时间格式与导出偏移一致。
- `export_song_lyrics()` 在导出前同样可走音频校准与手动偏移。

**段落信息 Excel**（`段落信息.xlsx`）

- 列：伴奏 ID、歌名、歌手、段落类型、起止时间、首句 / 末句歌词等。
- **特殊规则**：原起始为 0 的段落（如 intro）起始时间保持 `00:00.000`，结束时间仍参与校准偏移。

---

### `core/audio_calibration.py` — 音频参考校准

**职责**：用 MR 旋律参考音频对齐 JSON 音符时间轴，供 MIDI / 歌词 / 段落导出共用。

**流程**

1. 按 `original_key` 选取 `file_mr_mel_m` 或 `file_mr_mel_w` 直链，下载或定位本地缓存。
2. ffmpeg 转为 PCM WAV，librosa 分析前 30 秒能量包络，检测首个可感知旋律音与起音标记。
3. 在 MIDI 音符起始序列中寻找与音频起音最匹配的对齐点，计算 `offset_ms`。
4. `resolve_export_time_offset()` 将手动偏移与校准偏移相加，由 `apply_song_time_offset()` 统一应用到 `SongData`。

MIDI 导出、歌词导出、段落导出均可独立开关「音频参考校准」。

---

### `core/audio_downloader.py` — 音频资源下载

**职责**：按 JSON 中的 MR 直链下载音频，可选多轨混音后转码输出。

**内容类型**

- 伴奏（harmony）、旋律（melody）、鼓轨（drum）及其组合混音
- 调性：原始 / 男调 / 女调（由 `original_key` 与字段后缀 `_m` / `_w` 决定）

**处理流程**

1. `resolve_audio_track_urls()` 解析待下载 URL。
2. `download_cached_audio()` 缓存至 JSON 同目录下 `.ms_json_audio_cache/`。
3. ffmpeg `amix` 混音（`normalize=0`）；缺 drum 轨时自动跳过，仅输出已有轨。
4. 支持 WAV / FLAC（PCM 位深）、MP3 / M4A（比特率、AAC / ALAC）等输出参数。
5. 文件名可按曲名-歌手、含专辑、含 ID 等规则生成。

---

### `core/metadata_exporter.py` — 曲目元数据提取

**职责**：批量汇总曲目信息并下载关联资源文件。

**输出**

- `曲目元数据.xlsx`：伴奏 ID、多语言曲名 / 歌手 / 专辑、主曲速、曲速变化、罗马音信息、各资源直链与本地路径等。
- 中文子文件夹：`专辑封面`、`男调旋律`、`女调旋律`、`男调伴奏`、`女调伴奏`、`鼓轨`、`男调鼓轨`、`女调鼓轨`。
- 封面按 Content-Type / 魔数识别真实格式（URL 无后缀时也能保存为 `.jpg` 等）。

---

## 界面层 `ui/`

| 文件 | 说明 |
|------|------|
| `main_window.py` | 主窗口与导航；内嵌 `ExportPage`（MIDI 导出页）及后台 `ExportWorker` |
| `audio_download_page.py` | 音频下载参数与批量任务 |
| `metadata_export_page.py` | 元数据提取页 |
| `lyric_export_page.py` | 歌词导出、段落 Excel、校准选项 |
| `widgets.py` | 共用控件：`DragLineEdit`（拖入路径）、紧凑下拉框、偏移 SpinBox、`BatchProgressPanel` |

界面层负责参数收集、线程调度与结果提示；业务逻辑均在 `core/` 中实现。

## 入口 `main.py`

初始化 PyQt6 应用、Fluent 主题、窗口图标与 `MainWindow`，无业务逻辑。

## 辅助脚本 `scripts/`

| 脚本 | 用途 |
|------|------|
| `verify_midi_timing.py` | 校验导出 MIDI 的音符与段落 marker 绝对时间是否与 JSON 一致（±2 ms）；支持写入 / 不写入速度两种模式 |
| `create_icon.py` | 生成应用图标 |

```bash
python scripts/verify_midi_timing.py
```

## 目录结构

```
MS_json/
├── main.py                 # 程序入口
├── build.bat               # PyInstaller 打包脚本
├── MS_json.spec            # 打包配置（内置 ffmpeg）
├── requirements.txt
├── icon.ico
├── core/
│   ├── parser.py           # JSON 解析与 SongData 模型
│   ├── midi_exporter.py    # MIDI 导出
│   ├── lyric_exporter.py   # 歌词与段落 Excel
│   ├── audio_calibration.py# 音频参考校准
│   ├── audio_downloader.py # MR 音频下载与混音
│   └── metadata_exporter.py# 元数据 Excel 与资源下载
├── ui/
│   ├── main_window.py      # 主窗口 + MIDI 导出页
│   ├── audio_download_page.py
│   ├── metadata_export_page.py
│   ├── lyric_export_page.py
│   └── widgets.py
└── scripts/
    ├── verify_midi_timing.py
    └── create_icon.py
```

## 依赖说明

| 包 | 用途 |
|----|------|
| PyQt6 / PyQt6-Fluent-Widgets | 图形界面 |
| mido | MIDI 读写 |
| openpyxl | Excel 导出 |
| librosa / numpy | 音频包络分析与校准 |
| pyphen / syllables | 英文歌词音节划分 |

## 使用说明与边界情况

- **Other 轨**仅有音符、无独立歌词；合并同轨时可能从相邻 A/B 歌词延伸延音符 `-`。
- 勾选「删除疑似非旋律音符」会同时过滤对应时间范围内的歌词。
- 合唱段落在 JSON 中 A/B 各有一份相同歌词；导出「全部声部」歌词时会按时间去重。
- 不勾选「写入速度信息」时，MIDI 指挥轨与音符 tick 均按 120 BPM 处理，**回放毫秒位置与 JSON 一致**；勾选后写入完整 tempo map，tick 按分段速度换算，回放时刻仍与 JSON 一致。
- 音频校准依赖可访问的 MR 旋律直链或同目录本地文件；失败时可在各页面关闭「音频参考校准」仅使用手动偏移。
