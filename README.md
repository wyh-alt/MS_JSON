# MS JSON 导出工具

从 KTV 打分系统的 MS JSON 文件中提取旋律、歌词与段落信息，导出为 MIDI 或常见歌词格式。提供图形界面，支持批量处理。

## 环境要求

- Windows 10 及以上
- Python 3.10+（源码运行或自行打包时）

## 安装与运行

```bash
pip install -r requirements.txt
python main.py
```

打包为可执行文件：

```bash
build.bat
```

产物位于 `dist/MS_json.exe`。

## 输入文件

支持拖入或选择单个 JSON 文件、文件夹（递归扫描）。仅处理包含 `mnote.note` 与 `mnote.section` 的有效 JSON。

## 可提取资源

从 JSON 的 `mnote` 及相关字段中可读取：

| 类型 | 说明 |
|------|------|
| 旋律音符 | 起止时间、音高；声部标记 `isPartA` / `isPartB`（双 false 为 Other 轨） |
| 歌词 | 原文 `ori`、韩文 `ko`、罗马音 `rom`、英文 `en`；按 A/B 声部分轨 |
| 段落 | 结构名（intro / verse / chorus / bridge / rap / interlude 等）、起止时间、演唱声部 |
| 元数据 | 曲名、歌手（原文 / 韩文 / 英文）、曲库 ID（`mr_id`） |
| 速度 | `tempos` 中的 BPM（可选写入 MIDI） |

时间单位均为毫秒，导出时与 JSON 绝对时间一致。

## 功能概览

应用包含两个页面：**MIDI 导出**、**歌词导出**。

### MIDI 导出

导出 KTV 常用 Type 1 MIDI：Track 0 为拍号与速度，后续轨为旋律（及歌词）。

**声部模式**

| 模式 | 输出 |
|------|------|
| 合并导出（同轨） | A + B + Other 合并为一条旋律轨 |
| 合并导出（分轨） | A、B、Other 各占一轨（无 Other 音符时不建轨） |
| 分别导出 | 固定生成 `A声部.mid`、`B声部.mid`、`Other.mid` |
| 仅 A 声部 / 仅 B 声部 | 只导出对应声部 |

**常用选项**

- 写入速度信息：使用 JSON 原曲 BPM；不勾选则固定 120 BPM
- 写入歌词：歌词事件与音符同 tick 对齐（UTF-8）
- 歌词粒度：单词级（英文整词，连续音写 `-`）或与 JSON 条目一致
- 音符降低八度：默认开启
- 写入段落标记：在旋律轨写入 section marker
- 删除 Rap 段落音符
- 删除疑似非旋律音符：同音高占位、演唱音域外音符等（可选）
- 整体偏移：全部音符与歌词时间整体平移（毫秒）

输出文件名：`{曲名}_{mr_id}-{模式后缀}.mid`

### 歌词导出

**歌词格式**：KSC 小灰熊 (.txt)、KSC (.ksc)、TXT 分句、LRC、CSV

**其他选项**

- 歌词内容、声部（全部 / A / B）、歌名与歌手语言
- KSC 格式支持字符/单词中括号选项
- 整体偏移（毫秒）

**段落信息**：可将多首曲目的段落汇总为 `段落信息.xlsx`（歌名、歌手、段落类型、起止时间、首末句歌词）。

输出文件名示例：`{曲名}_{mr_id}-歌词.txt`

## 目录结构

```
MS_json/
├── main.py              # 程序入口
├── core/
│   ├── parser.py        # JSON 解析
│   ├── midi_exporter.py # MIDI 导出
│   └── lyric_exporter.py# 歌词与段落导出
├── ui/                  # 图形界面
├── scripts/             # 校验等辅助脚本
└── requirements.txt
```

## 校验脚本

检查导出 MIDI 的音符与段落 marker 是否与 JSON 时间一致：

```bash
python scripts/verify_midi_timing.py [JSON路径或目录]
```

## 说明

- Other 轨仅有音符、无独立歌词；合并同轨时可能从相邻 A/B 歌词延伸延音符 `-`
- 勾选「删除疑似非旋律音符」会同时过滤对应时间范围内的歌词
- 合唱段落在 JSON 中 A/B 各有一份相同歌词；导出「全部声部」歌词时会按时间去重
