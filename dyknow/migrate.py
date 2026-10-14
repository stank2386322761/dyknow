"""把旧版 DyKnow 的笔记复制成新格式。只读旧文件，绝不修改或删除。

保留：转录正文、AI 写的一句话总结和要点、作者、数据、分类（变成标签）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import note
from .asr import split_plain
from .douyin import Video, extract_tags
from .store import FOLDER_FAVORITES, INDEX_NAME, Store

_ID_IN_NAME = re.compile(r"_(\d{15,25})\.md$")
_PLACEHOLDER = re.compile(r"^_?(待模型填写|待转录\.\.\.|转录失败|待总结|待生成)_?$")
_OLD_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


@dataclass
class MigrateResult:
    copied: int = 0
    skipped_existing: int = 0
    skipped_other: int = 0
    organized: int = 0
    errors: list[str] = field(default_factory=list)


def _old_meta(text: str) -> dict:
    m = _OLD_FM.match(text)
    if not m:
        return {}
    try:
        import yaml
        data = yaml.safe_load(m.group(1))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
    except Exception:  # noqa: BLE001
        pass
    return note.read_meta(text)


def _section(body: str, heading: str) -> str:
    m = re.search(rf"^##\s*{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)", body, re.DOTALL | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _sub(section: str, name: str) -> str:
    m = re.search(rf"^###\s*{re.escape(name)}\s*\n(.*?)(?=^###\s|\Z)", section, re.DOTALL | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _clean(block: str) -> str:
    lines = []
    for ln in block.splitlines():
        s = ln.strip()
        if not s or s == "---" or _PLACEHOLDER.match(s):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def _int(v) -> int:
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0


def _duration_ms(v) -> int:
    m = re.fullmatch(r"(\d+):(\d{2})", str(v or "").strip())
    return (int(m.group(1)) * 60 + int(m.group(2))) * 1000 if m else 0


def _fill(template: str, heading: str, content: str) -> str:
    """把内容写到模板里某个小标题下（替换掉 AI 说明注释）"""
    pat = re.compile(rf"(^##\s*{re.escape(heading)}[ \t]*\n)(<!--.*?-->[ \t]*\n?)?", re.MULTILINE | re.DOTALL)
    m = pat.search(template)
    if not m:
        return template
    return template[:m.start()] + m.group(1) + content.strip() + "\n" + template[m.end():]


def convert(text: str, path: Path, template: str) -> tuple[Video, str, str, str, str] | None:
    meta = _old_meta(text)
    vid = str(meta.get("source_id") or meta.get("video_id") or "").strip()
    if not re.fullmatch(r"\d{15,25}", vid):
        m = _ID_IN_NAME.search(path.name)
        vid = m.group(1) if m else ""
    if not vid:
        return None
    body = text[_OLD_FM.match(text).end():] if _OLD_FM.match(text) else text

    title = str(meta.get("title") or "").strip()
    if not title:
        h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = h1.group(1).strip() if h1 else ""
    category = str(meta.get("category") or "").strip()
    tags = [t for t in extract_tags({}, title)]
    if category and category.lower() != "none" and category not in tags:
        tags.append(category)
    video = Video(id=vid, desc=title, author=str(meta.get("author") or ""),
                  duration_ms=_duration_ms(meta.get("duration")),
                  likes=_int(meta.get("likes")), comments=_int(meta.get("comments")),
                  shares=_int(meta.get("shares")), tags=tags)

    # 旧的转录
    transcript = _clean(_section(body, "📝 完整转写"))
    t_status = str(meta.get("transcript_status") or "").strip()
    legacy = str(meta.get("status") or "").strip()
    if transcript:
        status = note.TRANSCRIBED
    elif t_status == "failed" or legacy == "failed":
        status = note.FAILED
    else:
        status = note.PENDING

    # 旧的 AI 摘要
    ai = _section(body, "🤖 AI 摘要")
    one = _clean(_sub(ai, "一句话总结"))
    if not one:
        ov = _section(body, "📌 概述")
        one = _clean("\n".join(ln.lstrip("> ").strip() for ln in ov.splitlines()))
    points = _clean(_sub(ai, "要点"))
    quotes = _clean(_sub(ai, "金句"))
    if quotes:
        first = quotes.splitlines()[0].lstrip("-*• ").strip()
        points = (points + "\n" if points else "") + f"- 金句：{first}"
    filled = template
    if one:
        filled = _fill(filled, "一句话", one.splitlines()[0])
    if points:
        filled = _fill(filled, "要点", points)
    if transcript and one and "<!-- AI" not in filled:
        status = note.ORGANIZED
    day = str(meta.get("date") or "")[:10] or "0000-00-00"
    return video, filled, status, transcript if transcript else "", day


def run(old_root: Path, store: Store, template: str, dry_run: bool = False) -> MigrateResult:
    res = MigrateResult()
    old_root = Path(old_root)
    store.scan()
    for p in sorted(old_root.rglob("*.md")):
        if p.name in ("知识库索引.md", "索引.md", INDEX_NAME) or any(part.startswith(".") for part in p.relative_to(old_root).parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
            out = convert(text, p, template)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{p.name}：{e}")
            continue
        if out is None:
            res.skipped_other += 1
            continue
        video, filled, status, transcript, day = out
        if store.find(video.id):
            res.skipped_existing += 1
            continue
        res.copied += 1
        if status == note.ORGANIZED:
            res.organized += 1
        if dry_run:
            continue
        new_text = note.render(video, filled, status)
        if transcript:
            new_text = note.replace_block(new_text, "transcript",
                                          note.transcript_block(split_plain(transcript), extra="旧版转录，无时间戳"))
        elif status == note.FAILED:
            new_text = note.replace_block(new_text, "transcript", "> [!note]- 转写\n> 转录失败（旧版）")
        path = store.folder(FOLDER_FAVORITES) / note.file_name(video, day)
        note.write_atomic(path, new_text)
        store.ids[video.id] = path
    return res
