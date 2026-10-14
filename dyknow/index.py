"""自动生成「索引.md」：按文件夹分组，每条一行（标题 + AI 写的一句话）。每次命令结束自动更新。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from . import note
from .store import INDEX_NAME, Store


def _link_text(s: str) -> str:
    return s.replace("|", "｜").replace("[", "（").replace("]", "）")[:50]


def build(store: Store) -> tuple[str, Counter]:
    store.scan()
    infos = store.notes()
    counts = Counter(i.status or "未知" for i in infos)
    groups: dict[str, list[note.NoteInfo]] = defaultdict(list)
    for i in infos:
        rel = i.path.relative_to(store.root)
        groups[" / ".join(rel.parts[:-1]) or "其他"].append(i)

    order = [note.ORGANIZED, note.TRANSCRIBED, note.IMAGE, note.PENDING, note.FAILED, note.NO_SPEECH]
    summary = " · ".join(f"{s} {counts[s]}" for s in order if counts.get(s))
    lines = [
        "# 抖音知识库索引",
        "",
        f"> 共 {len(infos)} 条" + (f" · {summary}" if summary else "")
        + f"（{datetime.now():%Y-%m-%d %H:%M} 自动生成，不要手动修改）",
        "",
    ]
    for group in sorted(groups):
        items = sorted(groups[group], key=lambda i: i.path.name, reverse=True)
        lines += [f"## {group}（{len(items)}）", ""]
        for i in items:
            rel = i.path.relative_to(store.root).with_suffix("").as_posix()
            line = f"- [[{rel}|{_link_text(i.title)}]]"
            if i.summary:
                line += f" — {i.summary}"
            elif i.status not in (note.ORGANIZED,):
                line += f" `{i.status}`"
            lines.append(line)
        lines.append("")
    return "\n".join(lines), counts


def rebuild(store: Store) -> Counter:
    text, counts = build(store)
    if store.root.is_dir():
        note.write_atomic(store.root / INDEX_NAME, text)
    return counts
