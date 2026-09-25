# DyKnow

抖音视频 → 本地知识库 Markdown 笔记。

自动抓取、下载、语音转录、生成 Obsidian 兼容笔记，并把笔记按分类归档、生成索引。
**AI 摘要由调用方的模型完成**，DyKnow 本身不调用任何外部 AI API。

```text
抖音内容 → 抓取 → 下载视频 → 语音转录 → Markdown 笔记 → 模型阅读摘要 → 分类归档 → 索引
```

## 职责划分

| 谁 | 负责什么 |
|---|---|
| **程序** | 登录、抓取、下载、转录、状态记录、去重、转录哈希、分类注册表、文件移动、计数统计、索引重建 |
| **模型** | 阅读完整转录、生成一句话总结与要点、提取金句、判断分类建议 |

程序不做理解，模型不做搬运和计数。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 2. 下载转录模型（ggml-tiny.bin 74MB，或 ggml-small.bin 465MB）
#    放入 data/models/ 目录

# 3. 环境检测
python scripts/check_env.py

# 4. 扫码登录（Cookie 持久化）
python -m dyknow login

# 5. 开始使用
python -m dyknow parse "7.64 ... https://v.douyin.com/xxx/ ..."   # 单视频
python -m dyknow sync                                            # 同步收藏
python -m dyknow transcribe                                      # 批量转录
python -m dyknow browse -k "AI工具" --count 30                    # 关键词浏览
python -m dyknow organize                                        # 按分类归档
python -m dyknow index                                           # 重建索引
```

### 虚拟环境解释器

文档中的命令统一写作 `python -m dyknow <command>`，在虚拟环境里按平台替换：

```text
Windows:      .venv/Scripts/python.exe -m dyknow <command>
macOS/Linux:  .venv/bin/python        -m dyknow <command>
```

## 命令

| 命令 | 说明 |
|------|------|
| `login` | 扫码登录 |
| `status` | 查看登录、转录进度与 AI 处理进度 |
| `parse "<文本>"` | 单视频解析（场景 A） |
| `sync [--count N] [--transcribe] [--full]` | 增量同步收藏（场景 B） |
| `transcribe [--count N] [--retry-failed]` | 批量转录，支持断点续转 |
| `browse -k "关键词" [--count N] [--transcribe]` | 关键词浏览（场景 C） |
| `organize [--dry-run] [--migrate]` | 按分类安全移动笔记（场景 D） |
| `index [--no-category-index]` | 全量重建知识库索引 |
| `reset --target ...` / `night` | 重置任务 / 夜间批量转录 |

`organize` / `index` / `taxonomy` 支持 `--output <知识库根目录>`；
也可用环境变量 `DYKNOW_OUTPUT_DIR`。

## 目录语义

```text
<输出目录>/
├── 未处理/          ← 尚未完成 AI 知识处理
│                       （还没转录，或已转录但还没写摘要）
└── <知识库名称>/     ← 已处理并归档
    ├── 待聚类/      ← 已写摘要，但暂无稳定分类
    ├── <分类1>/
    └── 分类2/
```

`未处理/` 和 `待聚类/` 是两件事，不要混用：

- **未处理**：AI 还没做知识处理
- **待聚类**：AI 处理完了，但同类内容还没累计到 3 条，暂不单独建分类

新生成的笔记默认写入 `未处理/`；`organize` 再按分类把它们搬进知识库目录。

## 笔记状态

每条笔记的 frontmatter 用两个维度表达进度：

```yaml
transcript_status: pending | done | failed      # 转录维度
processing_status: pending | done | needs_review # AI 知识处理维度
```

| transcript_status | processing_status | 含义 |
|---|---|---|
| `pending` | `pending` | 只有元数据，待转录 |
| `done` | `pending` | 已转录，等模型写摘要 |
| `done` | `done` | 已完成 |
| `done` | `needs_review` | 转录内容变了，摘要需复核 |
| `failed` | `pending` | 转录失败（可重试） |

另外还有 `source_id`（稳定唯一键）、`category`（分类建议）、
`transcript_hash` / `summarized_from_hash`（幂等判断）等字段。

## 分类注册表

知识库根目录的 `taxonomy.yaml` 是机器可读的分类注册表：

```yaml
version: 1
categories:
  - name: AI工具与开发
    aliases: [AI工具, AI开发, AI编程]
    children:
      - name: Agent与工作流
        aliases: [Agent, MCP]
```

作用：

- 模型只需在笔记里写 `category: AI工具`，程序按 `aliases` 归一到一个正式分类
- 避免出现 `AI工具` / `AI开发` / `AI编程工具` 这类近义目录
- 新分类必须累计 **3 条**同类内容才会自动注册，单条内容先进 `待聚类/`

查看当前分类：

```bash
python -m dyknow taxonomy
python -m dyknow taxonomy --paths    # 只输出分类路径
```

> 若环境未安装 PyYAML，程序自动改用同结构的 `taxonomy.json`，功能不受影响。

## 索引

```bash
python -m dyknow index
```

索引**由程序全量重建**，不要手工编辑：

- 根索引 `知识库索引.md`：概览统计 + 每个分类一行（标题 + 一句话总结）
- 分类条目超过 10 条时，额外生成该分类的 `索引.md`（含要点与金句）
- 每条链接按文件真实相对路径生成，移动文件后重新执行即正确
- 索引文件自身不会被计入统计
- 个别坏文件只产生警告，不会让整个索引失败

## 新旧数据兼容

| 情况 | 行为 |
|---|---|
| 旧 `status: indexed / transcribed / done` | 自动映射到新的两个维度，笔记无需改动 |
| 旧字段 `status` | 继续写出，保持向后兼容 |
| 旧 `未分类/` 目录 | **默认原样保留**，可读取、可索引，不会自动搬动 |
| 增量迁移旧目录 | `python -m dyknow organize --migrate`（建议先加 `--dry-run` 预览） |

迁移建议：

```bash
python -m dyknow organize --dry-run --migrate   # 先看计划，不改文件
python -m dyknow organize --migrate             # 确认后执行
python -m dyknow index                          # 重建索引
```

## 依赖

- **运行时**：Python 3.11+、requests、playwright、pyyaml
- **转录**：pywhispercpp（ggml-tiny 74MB / ggml-small 465MB，CPU/GPU 通用）
- **音频提取**：ffmpeg（推荐）或 av 库
- **AI 摘要**：由调用方模型完成，无需额外配置

## 项目结构

```text
DyKnow/
├── SKILL.md              # 模型指令（路由与约束）
├── agents/openai.yaml    # agent 入口配置
├── references/           # 按需加载的详细规则
│   ├── workflows.md      # A/B/C/D 场景流程
│   ├── note-schema.md    # frontmatter 与状态字段
│   ├── summarization.md  # 摘要 / 金句规则
│   ├── taxonomy.md       # 分类与注册表
│   └── index.md          # 索引结构
├── dyknow/               # 核心包
│   ├── main.py           # CLI 入口
│   ├── login.py          # 扫码登录 + Cookie
│   ├── scraper.py        # 收藏抓取 + 关键词搜索
│   ├── downloader.py     # 视频下载
│   ├── transcriber.py    # 语音转录
│   ├── parser.py         # 单视频解析
│   ├── syncer.py         # 同步协调 / 断点续转
│   ├── generator.py      # 笔记生成与回填
│   ├── frontmatter.py    # 轻量 frontmatter 读写
│   ├── state.py          # 状态模型 + 幂等哈希
│   ├── taxonomy.py       # 分类注册表
│   ├── indexer.py        # 索引全量重建
│   ├── organizer.py      # 安全归档与移动
│   ├── layout.py         # 目录语义常量
│   ├── db.py             # SQLite 状态
│   └── config.py         # 配置
├── scripts/              # 辅助脚本
├── tests/                # 测试（run_all.py 一键运行）
└── DESIGN.md             # 设计文档
```

## 测试

```bash
python tests/run_all.py
python -m pytest tests/ -v
```

## 许可证

MIT
