"""
Markdown 笔记生成器 —— Obsidian 兼容格式

模板参考：抖音视频转MD文档最佳实践
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import config

logger = logging.getLogger("dyknow.generator")


# Windows/NTFS 文件名禁用字符
_WIN_FORBIDDEN_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
# 控制字符 (0x00-0x1F, 0x7F)
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')
# Windows 保留文件名（不区分大小写）
_WIN_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def safe_filename(title: str, max_len: int = 60) -> str:
    """
    将任意字符串转换为安全的文件名/路径片段。

    处理规则：
    1. 替换换行符、制表符为空格
    2. 移除所有控制字符 (0x00-0x1F, 0x7F)
    3. 将 Windows 禁用字符 \\ / : * ? " < > | 替换为 _（引号用单引号替代）
    4. 将方括号替换为圆括号（Obsidian 兼容）
    5. 去除首尾空格和句点（Windows 不允许）
    6. 若结果为空或为 Windows 保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9），追加下划线
    7. 截断到 max_len 并去除尾部空格
    """
    if not title:
        return "_"

    safe = title
    # 1. 换行/制表 → 空格
    safe = safe.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    # 2. 移除控制字符
    safe = _CONTROL_CHARS_RE.sub("", safe)
    # 3. Windows 禁用字符处理：引号用单引号替代，其余用下划线
    safe = safe.replace('"', "'")
    safe = _WIN_FORBIDDEN_CHARS_RE.sub("_", safe)
    # 4. 方括号 → 圆括号（Obsidian 兼容）
    safe = safe.replace("[", "(").replace("]", ")")
    # 5. 合并连续空格/下划线
    safe = re.sub(r'[_\s]{2,}', '_', safe)
    # 6. 去除首尾空格和句点
    safe = safe.strip(" .")
    # 7. 截断到 max_len
    safe = safe[:max_len].strip()
    # 8. 空结果或 Windows 保留名 → 追加下划线
    if not safe or safe.upper() in _WIN_RESERVED_NAMES:
        safe = (safe or "_") + "_"

    return safe


def _format_duration(ms: int) -> str:
    if not ms:
        return ""
    total_sec = ms // 1000
    return f"{total_sec // 60:02d}:{total_sec % 60:02d}"


def generate_note(
    title: str,
    aweme_id: str = "",
    author: str = "",
    author_id: str = "",
    duration: int = 0,
    likes: int = 0,
    comments: int = 0,
    shares: int = 0,
    plays: int = 0,
    cover_url: str = "",
    favorite_folder: str = "",
    favorite_time: str = "",
    transcript: str = "",
    output_dir: Optional[Path] = None,
) -> Path:
    """生成一条完整的 Obsidian 笔记"""

    today = datetime.now().strftime("%Y-%m-%d")
    safe_title = safe_filename(title)
    duration_str = _format_duration(duration)

    # 确定证据等级
    evidence_level = "transcript" if transcript else "indexed"
    status = "done" if transcript else "indexed"

    # 输出目录
    if output_dir is None:
        folder = favorite_folder.strip() if favorite_folder else "未分类"
        safe_folder = safe_filename(folder, 30) if folder != "未分类" else "未分类"
        output_dir = config.output_dir / safe_folder
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Frontmatter ──
    fm = f"""---
title: "{safe_title}"
video_id: "{aweme_id}"
source: "https://www.douyin.com/video/{aweme_id}"
author: "{author}"
author_id: "{author_id}"
platform: douyin
date: {today}
"""
    if duration_str:
        fm += f"duration: {duration_str}\n"
    if favorite_folder:
        fm += f'favorite_folder: "{favorite_folder}"\n'
    fm += f"""likes: {likes}
comments: {comments}
shares: {shares}
plays: {plays}
evidence_level: {evidence_level}
status: {status}
tags: [抖音, 收藏]
---"""

    # ── 正文 ──
    body = f"""
# {safe_title}

## 📌 概述

> 待模型总结后填入

## 📊 数据快照

| 指标 | 数值 |
|------|------|
| 作者 | {author} |
| 时长 | {duration_str or "未知"} |
"""
    if favorite_time:
        body += f"| 收藏时间 | {favorite_time} |\n"
    body += f"""| 👍 点赞 | {likes:,} |
| 💬 评论 | {comments:,} |
| 🔄 分享 | {shares:,} |
"""

    if cover_url:
        body += f"\n![封面]({cover_url})\n"

    body += f"""
> [!info] 视频信息
> - **作者**: [{author}](https://www.douyin.com/user/{author_id})
> - **链接**: [抖音原视频](https://www.douyin.com/video/{aweme_id})
> - **日期**: {today}
"""

    body += "\n---\n\n"

    # ── 转录文本 ──
    if transcript:
        body += f"## 📝 完整转写\n\n{transcript}\n\n"
    else:
        body += "## 📝 完整转写\n\n_待转录..._\n\n"

    body += "---\n\n"

    # ── 章节要点 ──
    body += "## 🎬 章节要点\n\n_待转录后生成_\n\n"

    body += "---\n\n"

    # ── 可复用素材 ──
    body += "## 💡 金句 / 可复用素材\n\n_待总结后填入_\n\n"

    body += "---\n\n## 🔗 相关链接\n\n"
    body += f"- [抖音原视频](https://www.douyin.com/video/{aweme_id})\n"
    if author_id:
        body += f"- [@{author}](https://www.douyin.com/user/{author_id})\n"

    # 写入
    filename = f"{safe_title}_{aweme_id}.md" if aweme_id else f"{safe_title}.md"
    output_path = output_dir / filename
    output_path.write_text(fm + body, encoding="utf-8")

    logger.info(f"笔记已生成: {output_path.name}")
    return output_path


def _atomic_write_text(path: Path, content: str):
    """
    原子写入文本：先写同目录临时文件，再 os.replace 替换。
    避免断点续转时 Ctrl+C / 进程崩溃导致原文件被截断。
    百度同步盘可能锁定目标文件，失败时重试。
    """
    import time as _time
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        # 同步盘可能锁定目标文件，重试几次
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt < 4:
                    _time.sleep(1)
                else:
                    raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def update_transcript(note_path: Path, transcript: str):
    """
    将转录文本写入已有笔记，更新证据等级。
    使用原子写：先写临时文件再 rename，避免断点续转时半截写入。
    """
    if not note_path.exists():
        return

    content = note_path.read_text(encoding="utf-8")

    section = f"## 📝 完整转写\n\n{transcript}\n" if transcript else "## 📝 完整转写\n\n_转录失败_\n"
    content = content.replace("## 📝 完整转写\n\n_待转录..._", section)
    content = content.replace("evidence_level: indexed", "evidence_level: transcript")
    # 兼容老 status 取值：indexed / pending_* / failed 都应升级为 transcribed
    content = re.sub(r"status:\s*\w+", "status: transcribed", content, count=1)

    _atomic_write_text(note_path, content)


def update_status(note_path: Path, status: str):
    if not note_path.exists():
        return
    content = note_path.read_text(encoding="utf-8")
    content = re.sub(r"status: \w+", f"status: {status}", content)
    note_path.write_text(content, encoding="utf-8")
