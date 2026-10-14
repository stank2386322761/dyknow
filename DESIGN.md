# DyKnow 3.1 架构

标准入口为 `dyknow` 或 `python -m dyknow`，CLI 输出 RESULT JSON。

- `config.py`：配置、数据目录和环境变量。
- `browser.py`：持久化浏览器登录、页面内接口请求、页面采集备用路径。
- `douyin.py`：作品、评论、分享链接与下载候选解析。
- `media.py`：下载、音轨检查、关键画面和均匀补帧。
- `asr.py`：GPU / CPU faster-whisper 与可选云端转录。
- `pipeline.py`：建笔记、预下载、转录、截图、错误状态和续转。
- `note.py` / `store.py` / `index.py`：笔记格式、跨来源去重、原子写入与索引。
- `migrate.py`：只读旧库并复制到新格式。

旧版模块、SQLite 工作流与定时任务的历史可在 Git 记录中查看。当前代码的已知问题和验证边界见 CHANGELOG.md 与 docs/local-validation-3.1.md。
