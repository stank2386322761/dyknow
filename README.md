# DyKnow

抖音视频 → 本地知识库 Markdown 笔记。

自动抓取、下载、语音转录、生成 Obsidian 兼容笔记，并把笔记按分类归档、生成索引。
**AI 摘要由调用方的模型完成**，DyKnow 本身不调用任何外部 AI API。

```text
默认更新：检查登录 → 同步新增收藏 → 只转录新增 → 每个视频一个 Markdown → 报告清单和路径。

用户明确要求整理时，再由其他 AI 写摘要、分类并重建索引。
```

## 职责划分

| 谁 | 负责什么 |
|---|---|
| **程序** | 登录、抓取、下载、转录、状态记录、去重、转录哈希、分类注册表、文件移动、计数统计、索引重建 |
| **模型** | 阅读完整转录、生成一句话总结与要点、提取金句、判断分类建议 |

程序不做理解，模型不做搬运和计数。

更新收藏使用 `python -m dyknow sync --transcribe`，默认检查最近 200 条，只转录本次新增视频。调用方报告新增清单、转录成功/失败/跳过及 MD 路径，到此结束；不自动摘要、归档或索引。断点续跑用 `transcribe --ids <本次ID清单>`。抓取失败不等于没有新增。

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

# 5. 设置笔记输出目录（只需一次，之后长期记住）
python -m dyknow config --set-output "X:/path/to/你的知识库"

# 6. 开始使用
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
| `organize [--dry-run] [--migrate] [--ids ...]` | 按分类安全移动笔记，可仅处理本次更新（场景 D） |
| `index [--no-category-index]` | 全量重建知识库索引 |
| `config [--set-output DIR]` | 查看 / 设置持久化配置（记住输出目录） |
| `reset [--target ...] [--ids ...]` / `night` | 重置任务 / 夜间批量转录 |

`--output <目录>` 对 `parse`、`sync`、`transcribe`、`browse`、`index`、`organize`、`taxonomy` 均可用，只影响本次运行。

## 配置

首次使用时设置一次笔记输出目录，之后长期记住：

```bash
python -m dyknow config --set-output "X:/path/to/你的知识库"
python -m dyknow config      # 查看当前配置与目录来源
```

输出目录解析优先级（高 → 低）：

```text
--output 参数  >  DYKNOW_OUTPUT_DIR 环境变量  >  data/config.json  >  <运行目录>/抖音收藏
```

`data/config.json` 位于数据目录内，而 `data/` 已被 `.gitignore` 忽略，不会进入版本库。

### 环境变量

| 变量 | 作用 |
|---|---|
| `DYKNOW_OUTPUT_DIR` | 笔记输出目录（优先级高于配置文件，适合临时切换） |
| `DYKNOW_DATA_DIR` | 数据目录（Cookie / 数据库 / 模型 / 配置文件） |
| `DYKNOW_GGML_MODEL` | 转录模型路径（默认 `data/models/ggml-tiny.bin`） |
| `DYKNOW_KEEP_MEDIA_CACHE` | 设为 `1` / `true` / `yes` 时保留视频与音频缓存，不自动删除 |

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

### 固定数据位置与登录复用

Cookie、数据库、配置、缓存和日志位于 `DYKNOW_DATA_DIR`；未设置时自动使用 Git 主项目目录下的 `data/`，所有工作树共享登录信息；非 Git 项目使用代码目录的 `data/`。环境变量可显式覆盖。MD 输出目录通过 `config --set-output` 保存，配置文件也在该数据目录内。不要复制 Cookie 到多个工作树或提交到 Git。

登录浏览器和收藏抓取浏览器是两个独立窗口，抓取窗口应复用 Cookie，无需再次扫码。`status` 只说明 Cookie 是否已保存，不证明服务端会接受它。接口无数据也可能是网络或抓取问题，不应直接认定必须重新登录。

收藏更新在同一浏览器会话中完成必要的扫码与抓取。`login` 复用已保存 Cookie；明确需要重新扫码或切换账号时用 `login --force`。全量同步仅刷新数据库元数据，保留已有 MD 和处理状态；重转录只更新转写章节及相关状态，转写变化时保留摘要并标记 `needs_review`，空结果保留旧正文。
