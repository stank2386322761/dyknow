# 工作流详解（A / B / C / D）

所有命令在项目根目录执行，统一使用 `python -m dyknow <command>`。
Windows 虚拟环境：`.venv/Scripts/python.exe -m dyknow <command>`；macOS/Linux：`.venv/bin/python -m dyknow <command>`。

标注说明：

- **[CLI]** = 由程序完成，模型不介入
- **[AI]** = 由模型完成，程序无法可靠替代

---

## 场景 A：单视频（用户粘贴带抖音链接的文字）

输入：用户在聊天中发来一段含抖音链接的分享文案。

```text
1. [CLI]  python -m dyknow parse "<用户的原话>"
          → 程序提取链接、取元数据、下载、转录、生成笔记
          → 输出末行形如：NOTE_PATH: <路径>
2. [CLI]  若命令输出提示未找到链接，直接把问题回给用户，不要臆造内容
3. [AI]   读取 NOTE_PATH 指向的文件
4. [AI]   若 frontmatter 中 transcript_status != done：
          → 转录未完成，不要凭元数据编造摘要，告知用户并建议
            python -m dyknow transcribe（或 parse 去掉 --no-transcribe）
5. [AI]   阅读 ## 📝 完整转写 全文，按 references/summarization.md 写：
          - ## 🤖 AI 摘要（一句话总结 / 要点 / 金句）
          - ## 🎬 章节要点
          - ## 💡 金句 / 可复用素材
6. [AI]   更新 frontmatter（见 references/note-schema.md）：
          processing_status: done
          transcript_hash: <程序给的 hash 或按规则计算>
          summarized_from_hash: 与 transcript_hash 相同
          category: "<建议分类>"（可为空）
7. [CLI]  python -m dyknow organize    # 程序按分类移动，处理同名冲突
8. [CLI]  python -m dyknow index       # 程序全量重建索引
```

单视频的 hash 与状态字段由 `parse` 生成笔记时写入；模型只需保证
`summarized_from_hash` 等于当前 `transcript_hash`。

---

## 场景 B：同步收藏

触发语：「同步我的收藏」「备份抖音内容」「把收藏都存下来」。

```text
1. [CLI]  python -m dyknow status          → 检查登录态与既有条目数
2. [CLI]  python -m dyknow login           → 未登录则扫码（浏览器只用于扫码）
3. [CLI]  python -m dyknow sync            → 增量同步元数据索引（快，秒级）
4. [AI]   向用户报告工作量：
          「共发现 N 条，其中 M 条需要下载和转录。转录属耗时步骤，
            将逐条处理，状态会保留，失败后可从中断处继续。」
          不要给出精确完成时间——除非程序确实读到了视频总时长。
5. [CLI]  python -m dyknow transcribe      → 批量下载 + 转录（支持断点续转）
          可选：--count N 限制本次条数；--retry-failed 纳入失败重试
6. [AI]   逐条读取已转录的笔记，写 AI 摘要、章节要点、金句，
          更新 processing_status / summarized_from_hash / category
7. [CLI]  python -m dyknow organize
8. [CLI]  python -m dyknow index
```

**幂等**：同一 `source_id` 已有成功转录，且转录内容未变时，不要重复转录。
`transcript_hash == summarized_from_hash` 且 `processing_status: done` 时，不要重复摘要。

### 批处理「处理收藏 / 整理知识库」（场景 D）

```text
1. [CLI]  扫描 未处理/ 下待处理笔记数量（status 命令或直接列目录）
2. [AI]   优先处理 transcript_status: done 的笔记（已可摘要）
          其次处理 transcript_status: pending 的 → 提示先转录
          transcript_status: failed 的 → 报告失败原因，不假装成功
3. [AI]   对每条笔记执行：
          a. 完整阅读 ## 📝 完整转写
          b. 生成 ## 🤖 AI 摘要（一句话总结 + 要点 + 金句）
          c. 填写 ## 🎬 章节要点 与 ## 💡 金句（若仍是占位符）
          d. 更新 frontmatter: processing_status: done
             summarized_from_hash: <当前 transcript_hash>
             category: "<建议分类>"
4. [CLI]  python -m dyknow organize --dry-run   → 先看移动计划，确认无误
5. [CLI]  python -m dyknow organize             → 执行移动
6. [CLI]  python -m dyknow index                → 全量重建索引
```

不要把长错误堆栈写进笔记正文；转录失败信息保存在数据库的 `last_error` 字段。

---

## 场景 C：主题浏览 / 自动研究

触发语：「帮我搜一下 XX 相关的抖音视频」「浏览最新的 XX 教程」。

```text
1. [CLI]  python -m dyknow browse -k "关键词" --count 30
          → 搜索关键词，抓取视频列表，生成索引笔记
          加 --transcribe 则同时下载 + 转录
2. [AI]   同场景 B 的第 4-8 步：报告工作量 → 转录 → 写摘要 → 归类 → 建索引
```

搜索可以指定任意关键词，等于把「抖音搜索」当作研究入口。

---

## 场景间共用规则

- 任何一步失败，先如实向用户报告，再给出可执行的下一步命令。
- 转录耗时步骤前必须提前告知用户，不要静默开始。
- 模型永远不直接搬动文件，移动一律交给 `organize`。
- 模型永远不手工维护索引，重建一律交给 `index`。
- **批量重跑一律用 `reset`**：它现在会清零 `attempts`（早期版本不清零，会让重跑被静默跳过）。子集重跑用 `reset --ids`，不要自己改库。详见 SKILL.md 第 8.1 节。
- **不要只看「转录结束」就宣布成功**：用 `status` 核对 `pending_index` 剩余数与实际成功数，把「成功/失败/跳过」三个数字都汇报给用户。
- **换转录模型（tiny → small 等）后必须显式重跑**，旧转录不会自动失效；见 SKILL.md 第 8.2 / 8.3 节。
