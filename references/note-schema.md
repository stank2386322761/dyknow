# 笔记结构与状态字段

## frontmatter 字段

程序生成笔记时写入以下字段。带「新增」标记的是 v2 引入的字段。

| 字段 | 类型 | 含义 |
|------|------|------|
| `title` | str | 安全化后的标题（去禁用字符） |
| `video_id` | str | 抖音视频 ID，与 `source_id` 同源 |
| `source` | str | 原视频链接 |
| `source_id` | str（新增） | 稳定唯一键，取值即 `aweme_id`，用于幂等与去重 |
| `author` | str | 作者昵称 |
| `author_id` | str | 作者 ID |
| `platform` | str | 固定 `douyin` |
| `date` | str | 笔记生成日期 `YYYY-MM-DD` |
| `duration` | str | 时长 `MM:SS`，未知则省略 |
| `favorite_folder` | str | 收藏夹名，可省略 |
| `likes` / `comments` / `shares` / `plays` | int | 数据快照 |
| `evidence_level` | str | `indexed`（仅元数据）/ `transcript`（有转录） |
| `transcript_status` | str（新增） | `pending` / `done` / `failed` |
| `processing_status` | str（新增） | `pending` / `done` / `needs_review` |
| `category` | str（新增） | 模型建议的分类；空串表示未判定 |
| `transcript_hash` | str（新增） | 完整转录正文的 SHA-256（取前 16 位十六进制） |
| `summarized_from_hash` | str（新增） | 生成 AI 摘要时所依据的 `transcript_hash` |
| `tags` | list | 固定 `[抖音, 收藏]` |
| `status` | str | **旧字段，仅为兼容保留** |

## 状态模型

v1 用单个 `status` 同时表达「转录进度」和「AI 知识处理进度」，v2 拆成两个维度：

```yaml
transcript_status: pending | done | failed     # 转录维度
processing_status: pending | done | needs_review   # AI 知识处理维度
```

组合含义：

| transcript_status | processing_status | 含义 | 处理动作 |
|---|---|---|---|
| `pending` | `pending` | 只有元数据 | 先转录 |
| `done` | `pending` | 已转录，待摘要 | 模型写摘要（优先队列） |
| `done` | `done` | 已完成 | 跳过 |
| `done` | `needs_review` | 摘要完成但有疑问 | 人工/模型复核 |
| `failed` | `pending` | 转录失败 | 报告错误，可重试 |

转录失败必须记为 `transcript_status: failed`，不得记成 `pending` 或 `done`。

## 旧笔记兼容策略

程序读取旧 frontmatter 时按如下映射，不要求用户改文件：

```text
status: indexed       → transcript_status: pending,  processing_status: pending
status: transcribed   → transcript_status: done,     processing_status: pending
status: done          → transcript_status: done,     processing_status: done
status: pending_*     → transcript_status: pending,  processing_status: pending
status: failed        → transcript_status: failed,   processing_status: pending
无 status 字段         → 有转录正文则视为 done/pending，否则 pending/pending
```

规则：

- 旧字段 `status` 仍然写出（保持向后兼容），但程序逻辑不再只依赖它做全部判断。
- 已有 `transcript_status` / `processing_status` 时以新字段为准。
- 缺少 `transcript_hash` 的旧笔记，程序按当前转录正文重新计算，不视为异常。

## 正文结构

```markdown
# <标题>

## 📌 概述
> 一句话总结（模型填写）

## 📊 数据快照
| 指标 | 数值 |
...

> [!info] 视频信息
> ...

---

## 📝 完整转写
（程序写入的转录正文；未转录时为 `_待转录..._`，失败时为 `_转录失败_`）

---

## 🎬 章节要点
（模型填写）

---

## 💡 金句 / 可复用素材
（模型填写）

---

## 🔗 相关链接
```

模型只在 `## 📌 概述`、`## 🎬 章节要点`、`## 💡 金句 / 可复用素材`、`## 🤖 AI 摘要` 这几个区块写入内容，**不要改动 `## 📝 完整转写` 正文**（哈希基于它计算，改动会导致重新摘要）。

## 摘要区块

`## 🤖 AI 摘要` 由模型追加，格式见 `references/summarization.md`。

## 文件命名

`<安全标题>_<source_id>.md`。`source_id` 保证不同视频即使标题相同也不会互相覆盖。
程序移动文件时不允许静默覆盖同名文件。
