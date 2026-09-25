"""
Markdown 笔记生成器 —— Obsidian 兼容格式

职责：
    1. 生成新笔记（frontmatter + 结构化正文）
    2. 把转录文本回填进已有笔记
    3. 提供原子写入，避免同步盘锁定 / 进程中断导致文件截断

frontmatter 字段与状态含义见 references/note-schema.md。
"""

import logging
import os
import re
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import frontmatter as fm_io
from .config import config
from .layout import UNPROCESSED_DIR
from .state import (
    PROCESSING_DONE,
    PROCESSING_NEEDS_REVIEW,
    PROCESSING_PENDING,
    TRANSCRIPT_DONE,
    TRANSCRIPT_FAILED,
    TRANSCRIPT_PENDING,
    TRANSCRIPT_SECTION_HEADING,
    legacy_status_of,
    transcript_hash,
)

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

# 正文区块标题
SUMMARY_HEADING = "🤖 AI 摘要"
OVERVIEW_HEADING = "📌 概述"
CHAPTER_HEADING = "🎬 章节要点"
QUOTE_HEADING = "💡 金句 / 可复用素材"

PLACEHOLDER_SUMMARY = "_待模型填写_"


def safe_filename(title: str, max_len: int = 60) -> str:
    """
    将任意字符串转换为安全的文件名/路径片段。

    处理规则：
    1. 替换换行符、制表符为空格
    2. 移除所有控制字符 (0x00-0x1F, 0x7F)
    3. 将 Windows 禁用字符 \\ / : * ? " < > | 替换为 _（引号用单引号替代）
    4. 将方括号替换为圆括号（Obsidian 兼容）
    5. 先去除首尾空白，再合并连续空白/下划线
       （先 strip 是为了避免首尾空格被压成下划线，产生 "_hello_" 这类结果）
    6. 去除首尾空格和句点（Windows 不允许）
    7. 若结果为空或为 Windows 保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9），追加下划线
    8. 截断到 max_len 并去除尾部空格与句点
    """
    if not title:
        return "_"

    safe = str(title)
    # 1. 换行/制表 → 空格
    safe = safe.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    # 2. 移除控制字符
    safe = _CONTROL_CHARS_RE.sub("", safe)
    # 3. Windows 禁用字符处理：引号用单引号替代，其余用下划线
    safe = safe.replace('"', "'")
    safe = _WIN_FORBIDDEN_CHARS_RE.sub("_", safe)
    # 4. 方括号 → 圆括号（Obsidian 兼容）
    safe = safe.replace("[", "(").replace("]", ")")
    # 5. 先去掉首尾空白，再合并连续空白/下划线
    safe = safe.strip()
    safe = re.sub(r'[_\s]{2,}', '_', safe)
    # 6. 去除首尾空格和句点
    safe = safe.strip(" .")
    # 7. 截断到 max_len
    safe = safe[:max_len].strip(" .")
    # 8. 空结果 → 占位符；Windows 保留名 → 追加下划线避免冲突
    if not safe:
        safe = "_"
    elif safe.upper() in _WIN_RESERVED_NAMES:
        safe = safe + "_"

    return safe


def _format_duration(ms: int) -> str:
    if not ms:
        return ""
    total_sec = int(ms) // 1000
    return f"{total_sec // 60:02d}:{total_sec % 60:02d}"


# ── 原子写入 ──────────────────────────────────


def atomic_write_text(path: Path, content: str):
    """
    原子写入文本：先写同目录临时文件，再 os.replace 替换。

    避免断点续转时 Ctrl+C / 进程崩溃导致原文件被截断。
    百度同步盘可能锁定目标文件，失败时重试。
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
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


# 旧名字保留，避免外部脚本引用失效
_atomic_write_text = atomic_write_text


# ── 生成新笔记 ────────────────────────────────


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
    source_id: str = "",
    category: str = "",
) -> Path:
    """生成一条完整的 Obsidian 笔记，返回文件路径"""

    today = datetime.now().strftime("%Y-%m-%d")
    safe_title = safe_filename(title)
    duration_str = _format_duration(duration)
    sid = source_id or aweme_id

    has_transcript = bool(transcript and transcript.strip())
    transcript_status = TRANSCRIPT_DONE if has_transcript else TRANSCRIPT_PENDING
    processing_status = PROCESSING_PENDING
    t_hash = transcript_hash(transcript) if has_transcript else ""
    evidence_level = "transcript" if has_transcript else "indexed"

    # 未指定输出目录时，新内容进入「未处理」，由 organize 再按分类归档
    if output_dir is None:
        output_dir = config.output_dir / UNPROCESSED_DIR
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── frontmatter ──
    meta: dict = {
        "title": safe_title,
        "video_id": aweme_id,
        "source_id": sid,
        "source": f"https://www.douyin.com/video/{aweme_id}",
        "author": author,
        "author_id": author_id,
        "platform": "douyin",
        "date": today,
    }
    if duration_str:
        meta["duration"] = duration_str
    if favorite_folder:
        meta["favorite_folder"] = favorite_folder
    meta.update({
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "plays": plays,
        "evidence_level": evidence_level,
        "transcript_status": transcript_status,
        "processing_status": processing_status,
        "category": category,
        "transcript_hash": t_hash,
        "summarized_from_hash": "",
        # 旧字段继续写出，保持向后兼容；新逻辑不再只依赖它做判断
        "status": legacy_status_of(transcript_status, processing_status),
        "tags": ["抖音", "收藏"],
    })

    # ── 正文 ──
    body = f"""
# {safe_title}

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

---

## {SUMMARY_HEADING}

### 一句话总结
{PLACEHOLDER_SUMMARY}

### 要点
{PLACEHOLDER_SUMMARY}

### 金句
{PLACEHOLDER_SUMMARY}

---

## {TRANSCRIPT_SECTION_HEADING}

{transcript if has_transcript else "_待转录..._"}

---

## {CHAPTER_HEADING}

{PLACEHOLDER_SUMMARY}

---

## {QUOTE_HEADING}

{PLACEHOLDER_SUMMARY}

---

## 🔗 相关链接

- [抖音原视频](https://www.douyin.com/video/{aweme_id})
"""
    if author_id:
        body += f"- [@{author}](https://www.douyin.com/user/{author_id})\n"

    filename = f"{safe_title}_{sid}.md" if sid else f"{safe_title}.md"
    output_path = output_dir / filename
    atomic_write_text(output_path, fm_io.dump_frontmatter(meta, body))

    logger.info("笔记已生成: %s", output_path.name)
    return output_path


# ── 回填转录 ──────────────────────────────────


def update_transcript(note_path: Path, transcript: str) -> bool:
    """
    把转录文本写入已有笔记，并同步状态字段。

    - 使用原子写，避免断点续转时半截写入
    - **frontmatter 逐行更新（frontmatter.set_field），不整体重新序列化**，
      因此笔记里的多行列表、嵌套字段、自定义字段都不会因为回填转录而丢失
    - 重新计算 transcript_hash；转录内容变化时把 processing_status 置为
      needs_review（旧摘要已失效，需要重新做 AI 知识处理）
    """
    note_path = Path(note_path)
    if not note_path.exists():
        logger.warning("笔记不存在，跳过回填: %s", note_path)
        return False

    text = fm_io.read_text(note_path)
    if not text:
        logger.warning("笔记读取失败，跳过回填: %s", note_path)
        return False

    meta, _body = fm_io.parse_frontmatter(text)

    new_hash = transcript_hash(transcript)
    prev_summarized = str(meta.get("summarized_from_hash") or "").strip()

    t_status = TRANSCRIPT_DONE if transcript else TRANSCRIPT_FAILED
    p_status = str(meta.get("processing_status") or PROCESSING_PENDING)
    if transcript and prev_summarized and prev_summarized != new_hash:
        # 转录内容已变化 → 旧摘要失效，需要复核
        if p_status == PROCESSING_DONE:
            p_status = PROCESSING_NEEDS_REVIEW

    updates = {
        "evidence_level": "transcript" if transcript else "indexed",
        "transcript_status": t_status,
        "processing_status": p_status,
        "transcript_hash": new_hash,
        "status": legacy_status_of(t_status, p_status),
    }

    new_text = text
    for key, value in updates.items():
        new_text = fm_io.set_field(new_text, key, value)

    content = transcript if transcript else "_转录失败_"
    new_text = fm_io.replace_section(new_text, TRANSCRIPT_SECTION_HEADING, content)

    atomic_write_text(note_path, new_text)
    return True


# ── 兼容旧接口 ────────────────────────────────


def update_status(note_path: Path, status: str):
    """
    直接改写笔记状态（保留旧接口，测试与外部脚本仍可使用）。

    会同时同步新的 transcript_status / processing_status 维度。
    """
    from .state import LEGACY_STATUS_MAP

    note_path = Path(note_path)
    if not note_path.exists():
        return
    updates = {"status": status}
    mapped = LEGACY_STATUS_MAP.get(str(status).strip().lower())
    if mapped:
        updates["transcript_status"], updates["processing_status"] = mapped
    fm_io.update_meta(note_path, updates)


def mark_transcript_failed(note_path: Path, error: str = "") -> bool:
    """
    在笔记 frontmatter 标记转录失败。

    - 只写状态，不把错误堆栈写进笔记正文（可诊断信息保存在数据库 last_error）
    - frontmatter 逐行更新，不整体重写，避免丢失复杂 / 未知字段
    - 转录失败绝不能被当成 pending 或 done
    """
    note_path = Path(note_path)
    if not note_path.exists():
        return False

    text = fm_io.read_text(note_path)
    if not text:
        return False

    meta, _body = fm_io.parse_frontmatter(text)
    p_status = str(meta.get("processing_status") or PROCESSING_PENDING)

    new_text = fm_io.set_field(text, "transcript_status", TRANSCRIPT_FAILED)
    new_text = fm_io.set_field(new_text, "processing_status", p_status)
    new_text = fm_io.set_field(
        new_text, "status", legacy_status_of(TRANSCRIPT_FAILED, p_status)
    )

    if "_待转录..._" in new_text:
        new_text = fm_io.replace_section(
            new_text, TRANSCRIPT_SECTION_HEADING, "_转录失败_"
        )

    atomic_write_text(note_path, new_text)
    return True
