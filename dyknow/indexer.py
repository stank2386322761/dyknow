"""
知识库索引 —— 全量重建

设计原则（见 references/index.md）：

    索引由程序全量重建，模型不手工统计、不手工维护。
    根索引只放一句话总结，不随收藏增长而塞入完整摘要；
    完整内容只保存在单条视频笔记里。

要求：
    - 只从真实 Markdown 文件 / frontmatter / 摘要区块读取状态
    - 不把索引文件自身当作视频笔记统计
    - 单个坏文件只产生警告，不让整体失败
    - Obsidian 链接按文件真实相对路径生成，移动文件后重新执行即正确
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import frontmatter as fm_io
from .layout import (
    CATEGORY_INDEX_FILENAME,
    INDEX_FILENAME,
    PENDING_CLUSTER_DIR,
    UNPROCESSED_DIR,
    is_index_file,
    is_unprocessed_dir,
)
from .state import (
    PROCESSING_DONE,
    PROCESSING_NEEDS_REVIEW,
    PROCESSING_PENDING,
    is_video_note,
    recover_source_id,
    recover_source_id_from_filename,
    resolve_state,
)
from .taxonomy import Taxonomy

logger = logging.getLogger("dyknow.indexer")

SUMMARY_HEADING = "🤖 AI 摘要"
OVERVIEW_HEADING = "📌 概述"
CHAPTER_HEADING = "🎬 章节要点"
QUOTE_HEADING = "💡 金句 / 可复用素材"

# 分类条目达到该数量时额外生成分类索引
CATEGORY_INDEX_THRESHOLD = 10

_SUMMARY_LINE_MAX = 120
_POINT_LINE_MAX = 160

_PLACEHOLDER_MARKERS = ("待模型", "待总结", "待转录", "待生成", "待填入")

# 每个分类在根索引中使用的图标
_ICONS = {
    "AI工具": "🤖", "AI视频": "🎬", "自媒体": "📱",
    "商业": "💰", "投资": "💰", "思维": "🧠",
    "生活": "🌈", "学习": "📚", "工作": "💼",
}


def _icon_for(name: str) -> str:
    for k, v in _ICONS.items():
        if k in name:
            return v
    return "📂"


def _is_placeholder(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return len(t) <= 40 and any(k in t for k in _PLACEHOLDER_MARKERS)


def _clean_line(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^[-*•]\s*", "", s)
    s = re.sub(r"^\d{1,2}[:：]\d{2}\s*[|｜\-—]?\s*", "", s)
    s = re.sub(r"^\d+\.\s*", "", s)
    s = s.replace("**", "")
    return s.strip()


def _clean_quote(line: str) -> str:
    s = _clean_line(line)
    s = re.sub(r"[（(]\s*转录原文\s*[）)]\s*$", "", s).strip()
    return s.strip('"“”').strip()


def _clip(text: str, limit: int) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


# ── 笔记信息 ──────────────────────────────────


@dataclass
class NoteInfo:
    """一条视频笔记的索引信息"""

    path: Path
    title: str = ""
    author: str = ""
    likes: int = 0
    source_id: str = ""
    # 是否恢复了稳定唯一 ID。v1 笔记可能没有 source_id，此时仍会索引，
    # 但不参与去重 / 覆盖判断（保守策略）。
    has_stable_id: bool = True
    raw_category: str = ""
    category: str | None = None       # 解析后的规范分类路径
    transcript_status: str = ""
    processing_status: str = ""
    summary: str = ""
    points: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    in_unprocessed_dir: bool = False
    in_pending_cluster_dir: bool = False
    warning: str = ""

    @property
    def is_processed(self) -> bool:
        """已完成 AI 处理且摘要与当前转录一致"""
        return self.processing_status == PROCESSING_DONE

    @property
    def is_pending(self) -> bool:
        """从未完成 AI 摘要 / 分类处理"""
        return self.processing_status == PROCESSING_PENDING

    @property
    def is_needs_review(self) -> bool:
        """曾完成过 AI 处理，但底层转录发生变化，需要重新复核"""
        return self.processing_status == PROCESSING_NEEDS_REVIEW

    @property
    def is_pending_cluster(self) -> bool:
        """已处理但分类未定"""
        return self.is_processed and not self.category

    @property
    def link_text(self) -> str:
        return self.title or self.path.stem


# ── 扫描 ──────────────────────────────────────


class IndexBuilder:
    """扫描知识库并全量重建索引"""

    def __init__(self, root: Path, taxonomy: Taxonomy | None = None):
        self.root = Path(root)
        self.taxonomy = taxonomy if taxonomy is not None else Taxonomy(self.root).load()
        self.notes: list[NoteInfo] = []
        self.warnings: list[str] = []

    # ── 扫描 ──

    def scan(self) -> list[NoteInfo]:
        self.notes = []
        self.warnings = []
        if not self.root.exists():
            self.warnings.append(f"知识库目录不存在: {self.root}")
            return self.notes

        for md in sorted(self.root.rglob("*.md")):
            if not md.is_file():
                continue
            if is_index_file(md):
                # 索引文件自身不得计入统计
                continue
            rel = md.relative_to(self.root)
            if any(part.startswith(".") for part in rel.parts):
                # 跳过 .obsidian / .git 等隐藏目录
                continue
            info = self._parse_note(md)
            if info is not None:
                self.notes.append(info)

        # 未注册分类汇总提示（避免逐条刷屏）
        unregistered = sorted({
            n.raw_category for n in self.notes
            if n.is_processed and n.raw_category and not n.category
        })
        if unregistered:
            shown = "、".join(unregistered[:10])
            more = f" 等 {len(unregistered)} 个" if len(unregistered) > 10 else ""
            self.warnings.append(
                f"以下分类尚未注册，对应笔记暂归「待聚类」: {shown}{more}"
                "（运行 organize 后达到 3 条的会自动建类）"
            )

        return self.notes

    def _parse_note(self, path: Path) -> NoteInfo | None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            self.warnings.append(f"读取失败，已跳过: {path.name} ({e})")
            return None

        meta, body = fm_io.parse_frontmatter(text)

        # source_id 是 v2 才引入的字段；v1 笔记只有 video_id / source，
        # 还有些旧笔记载体连 ID 字段都没有，只能从文件名 `<标题>_<id>.md` 恢复。
        # 全部恢复不出来时仍按路径索引（保守：不参与去重/覆盖判断），
        # 只有确认不属于本工具的普通 Markdown 才排除。
        source_id = recover_source_id(meta) or recover_source_id_from_filename(path)
        if not source_id and not is_video_note(meta):
            self.warnings.append(f"非视频笔记，未计入索引: {path.name}")
            return None
        if not source_id:
            self.warnings.append(
                f"无法恢复唯一 ID，已按路径安全索引（不参与去重/覆盖）: {path.name}"
            )

        t_status, p_status = resolve_state(meta, body)

        info = NoteInfo(
            path=path,
            title=str(meta.get("title") or path.stem),
            author=str(meta.get("author") or ""),
            likes=_as_int(meta.get("likes")),
            source_id=source_id,
            has_stable_id=bool(source_id),
            raw_category=str(meta.get("category") or "").strip(),
            transcript_status=t_status,
            processing_status=p_status,
            in_unprocessed_dir=is_unprocessed_dir(path.parent.name),
            in_pending_cluster_dir=path.parent.name == PENDING_CLUSTER_DIR,
        )

        if info.raw_category and info.raw_category.lower() != "none":
            info.category = self.taxonomy.resolve(info.raw_category)
            if info.category is None:
                info.warning = f"分类未注册，暂归待聚类: {info.raw_category}"

        info.summary = self._extract_summary(body)
        info.points = self._extract_points(body)
        info.quotes = self._extract_quotes(body)
        return info

    # ── 摘要解析 ──

    def _extract_summary(self, body: str) -> str:
        sec = fm_io.extract_section(body, SUMMARY_HEADING)
        for pattern in (
            r"###\s*一句话总结\s*\n+(.*?)(?=\n###|\Z)",
            r"\*\*一句话总结[：:]\*\*\s*(.+)",
        ):
            m = re.search(pattern, sec or "", re.DOTALL)
            if m:
                val = _clean_line(m.group(1).split("\n")[0])
                if val and not _is_placeholder(val):
                    return _clip(val, _SUMMARY_LINE_MAX)

        # 兼容 v1 笔记：## 📌 概述 的引用行
        sec = fm_io.extract_section(body, OVERVIEW_HEADING)
        for line in (sec or "").splitlines():
            s = line.strip()
            if s.startswith(">"):
                val = _clean_line(s.lstrip(">").strip())
                if val and not _is_placeholder(val):
                    return _clip(val, _SUMMARY_LINE_MAX)
        return ""

    def _extract_points(self, body: str) -> list[str]:
        out: list[str] = []
        for heading in (CHAPTER_HEADING, SUMMARY_HEADING):
            sec = fm_io.extract_section(body, heading)
            if not sec:
                continue
            m = re.search(r"###\s*要点\s*\n(.*?)(?=\n###|\Z)", sec, re.DOTALL)
            target = m.group(1) if m else (sec if heading == CHAPTER_HEADING else "")
            for line in target.splitlines():
                if not line.strip().startswith(("-", "*", "•")):
                    continue
                val = _clean_line(line)
                if val and not _is_placeholder(val):
                    out.append(_clip(val, _POINT_LINE_MAX))
            if out:
                break
        return out[:5]

    def _extract_quotes(self, body: str) -> list[str]:
        for heading in (QUOTE_HEADING, SUMMARY_HEADING):
            sec = fm_io.extract_section(body, heading)
            if not sec:
                continue
            m = re.search(r"###\s*金句\s*\n(.*?)(?=\n###|\Z)", sec, re.DOTALL)
            target = m.group(1) if m else (sec if heading == QUOTE_HEADING else "")
            quotes = []
            for line in target.splitlines():
                if not line.strip().startswith(("-", "*", "•", ">")):
                    continue
                val = _clean_quote(line)
                if val and len(val) >= 8 and not _is_placeholder(val):
                    quotes.append(_clip(val, _POINT_LINE_MAX))
            if quotes:
                return quotes[:3]
        return []

    # ── 统计 ──

    def stats(self) -> dict:
        total = len(self.notes)
        # 三个状态互斥：done / pending / needs_review。
        # 注意：pending 必须用 is_pending（== pending）判定，
        # 不能用 `not is_processed`，否则 needs_review 会被同时计入 pending。
        processed = [n for n in self.notes if n.is_processed]
        pending = [n for n in self.notes if n.is_pending]
        needs_review = [n for n in self.notes if n.is_needs_review]
        pending_cluster = [n for n in self.notes if n.is_pending_cluster]

        by_category: dict[str, list[NoteInfo]] = {}
        for n in processed:
            if n.category:
                by_category.setdefault(n.category, []).append(n)

        return {
            "total": total,
            "processed": len(processed),
            "pending": len(pending),
            "pending_cluster": len(pending_cluster),
            "needs_review": len(needs_review),
            "by_category": by_category,
        }

    # ── 生成根索引 ──

    def build_root_index(self) -> str:
        st = self.stats()
        today = datetime.now().strftime("%Y-%m-%d")

        lines: list[str] = [
            "---",
            "title: 知识库索引",
            f"date: {today}",
            "generated_by: dyknow index",
            f"total: {st['total']}",
            "---",
            "",
            "# 知识库索引",
            "",
            (
                f"> 最后更新：{today} | 已处理 {st['processed']} "
                f"| 待处理 {st['pending']} | 待聚类 {st['pending_cluster']}"
                + (f" | 待复核 {st['needs_review']}" if st["needs_review"] else "")
            ),
            "",
        ]

        # 概览
        lines += ["## 概览", "", "| 分类 | 已处理 | 待处理 |", "| --- | ---: | ---: |"]
        unprocessed_pending = sum(1 for n in self.notes if n.is_pending)
        if unprocessed_pending:
            lines.append(f"| {UNPROCESSED_DIR} | 0 | {unprocessed_pending} |")
        pending_cluster_done = sum(1 for n in self.notes if n.is_pending_cluster)
        if pending_cluster_done:
            lines.append(f"| {PENDING_CLUSTER_DIR} | {pending_cluster_done} | 0 |")
        if st["needs_review"]:
            lines.append(f"| 待复核 | {st['needs_review']} | 0 |")
        for cat in sorted(st["by_category"]):
            done = len(st["by_category"][cat])
            lines.append(f"| {_display_name(cat)} | {done} | 0 |")
        lines.append("")

        # 分类明细
        for cat in sorted(st["by_category"]):
            entries = st["by_category"][cat]
            icon = _icon_for(cat)
            lines += ["---", "", f"## {icon} {_display_name(cat)}（{len(entries)}条）", ""]
            for n in sorted(entries, key=lambda x: x.title):
                lines.append(self._root_entry_line(n))
            lines.append("")
            # 分类索引
            if len(entries) >= CATEGORY_INDEX_THRESHOLD:
                lines.append(
                    f"> 该分类条目较多，详见 [[{cat}/{CATEGORY_INDEX_FILENAME[:-3]}|{_display_name(cat)}索引]]"
                )
                lines.append("")

        # 待处理 / 待复核 / 待聚类
        pending_notes = [n for n in self.notes if n.is_pending]
        if pending_notes:
            lines += ["---", "", f"## ⏳ {UNPROCESSED_DIR}（{len(pending_notes)}条）", ""]
            lines += ["<details><summary>展开待处理列表</summary>", ""]
            for n in sorted(pending_notes, key=lambda x: x.title):
                lines.append(self._root_entry_line(n, with_summary=False))
            lines += ["", "</details>", ""]

        review_notes = [n for n in self.notes if n.is_needs_review]
        if review_notes:
            lines += [
                "---",
                "",
                f"## ♻️ 待复核（{len(review_notes)}条）",
                "",
                "> 曾完成过 AI 处理，但底层转录已变化，需要重新复核摘要；"
                "归档时保持当前位置不动。",
                "",
            ]
            for n in sorted(review_notes, key=lambda x: x.title):
                lines.append(self._root_entry_line(n))

        cluster_notes = [n for n in self.notes if n.is_pending_cluster]
        if cluster_notes:
            lines += [
                "---",
                "",
                f"## 🗂️ {PENDING_CLUSTER_DIR}（{len(cluster_notes)}条）",
                "",
                "> 已完成 AI 处理，但暂无稳定分类归属（同类内容累计 3 条后再建分类）",
                "",
            ]
            for n in sorted(cluster_notes, key=lambda x: x.title):
                lines.append(self._root_entry_line(n))

        lines += ["", "---", "", f"> 本索引由 `python -m dyknow index` 全量重建，请勿手工编辑。", ""]
        return "\n".join(lines)

    def _root_entry_line(self, n: NoteInfo, with_summary: bool = True) -> str:
        rel = n.path.relative_to(self.root).with_suffix("")
        link = f"[[{rel.as_posix()}|{_clip(n.link_text, 60)}]]"
        if with_summary and n.summary:
            return f"- {link} — {n.summary}"
        if n.author:
            return f"- {link} — {n.author}"
        return f"- {link}"

    # ── 生成分类索引 ──

    def build_category_index(self, category: str) -> str:
        st = self.stats()
        entries = st["by_category"].get(category, [])
        today = datetime.now().strftime("%Y-%m-%d")
        display = _display_name(category)

        lines = [
            "---",
            f"title: {display} 索引",
            f"date: {today}",
            "generated_by: dyknow index",
            "---",
            "",
            f"# {_icon_for(category)} {display}",
            "",
            f"> 最后更新：{today} | 共 {len(entries)} 条",
            "",
        ]
        for n in sorted(entries, key=lambda x: x.title):
            rel = n.path.relative_to(self.root).with_suffix("")
            lines.append(f"### [[{rel.as_posix()}|{_clip(n.link_text, 60)}]]")
            meta_bits = []
            if n.author:
                meta_bits.append(n.author)
            if n.likes:
                meta_bits.append(f"👍 {n.likes:,}")
            if meta_bits:
                lines.append(f"- {' | '.join(meta_bits)}")
            if n.summary:
                lines.append(f"- **一句话**: {n.summary}")
            for p in n.points[:3]:
                lines.append(f"- **要点**: {p}")
            if n.quotes:
                lines.append(f'- **金句**: “{n.quotes[0]}”（转录原文）')
            lines.append("")
        lines += ["---", "", "> 本索引由 `python -m dyknow index` 全量重建，请勿手工编辑。", ""]
        return "\n".join(lines)

    # ── 写出 ──

    def write(self, category_indexes: bool = True) -> dict:
        from .generator import atomic_write_text

        self.root.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []

        root_index = self.root / INDEX_FILENAME
        atomic_write_text(root_index, self.build_root_index())
        written.append(root_index)

        if category_indexes:
            st = self.stats()
            for cat, entries in st["by_category"].items():
                if len(entries) < CATEGORY_INDEX_THRESHOLD:
                    continue
                target_dir = self.taxonomy.target_dir(self.root, cat)
                target_dir.mkdir(parents=True, exist_ok=True)
                path = target_dir / CATEGORY_INDEX_FILENAME
                atomic_write_text(path, self.build_category_index(cat))
                written.append(path)

        return {
            "written": written,
            "stats": self.stats(),
            "warnings": self.warnings,
        }


def _display_name(category: str) -> str:
    parts = [re.sub(r"^\d+\s*[-_.、]\s*", "", p) for p in category.split("/")]
    return " / ".join(parts)


def _as_int(value) -> int:
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0


def rebuild_index(root: Path, category_indexes: bool = True) -> dict:
    """便捷入口：扫描并全量重建索引"""
    builder = IndexBuilder(root)
    builder.scan()
    return builder.write(category_indexes=category_indexes)
