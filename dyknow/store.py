"""知识库目录：按文件名里的视频 ID 找笔记。没有数据库，文件就是唯一的真相。"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import note
from .douyin import Comment, Video

# 抖音是纯数字 ID；其他平台带前缀：xhs-…、zh-a…、bili-BV…
_ID_IN_NAME = re.compile(r"_([a-z]{2,6}-[0-9A-Za-z]{3,40}|\d{6,25})\.md$")
INDEX_NAME = "索引.md"

FOLDER_FAVORITES = "收藏"
FOLDER_LINKS = "链接"
FOLDER_SEARCH = "搜索"
FOLDER_CREATOR = "博主"
FOLDER_RESEARCH = "对标"
FOLDER_HOT = "热榜"


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._ids: dict[str, Path] | None = None

    def scan(self) -> dict[str, Path]:
        ids: dict[str, Path] = {}
        if self.root.is_dir():
            for p in self.root.rglob("*.md"):
                rel = p.relative_to(self.root)
                if any(part.startswith(".") for part in rel.parts):
                    continue
                m = _ID_IN_NAME.search(p.name)
                if m and m.group(1) not in ids:
                    ids[m.group(1)] = p
        self._ids = ids
        return ids

    @property
    def ids(self) -> dict[str, Path]:
        if self._ids is None:
            self.scan()
        return self._ids  # type: ignore[return-value]

    def find(self, vid: str) -> Path | None:
        p = self.ids.get(vid)
        if p is not None and p.is_file():
            return p
        return None

    def folder(self, kind: str, name: str = "") -> Path:
        base = self.root / kind
        return base / note.safe_name(name, 40) if name else base

    def create(self, video: Video, folder: Path, template: str,
               comments: list[Comment] | None = None, day: str | None = None) -> tuple[Path, bool]:
        """新建笔记；已存在（任何文件夹里）就返回已有的。返回 (路径, 是否新建)"""
        existing = self.find(video.id)
        if existing:
            return existing, False
        status = note.IMAGE if video.is_image else note.PENDING
        path = folder / note.file_name(video, day or date.today().isoformat())
        note.write_atomic(path, note.render(video, template, status, comments))
        self.ids[video.id] = path
        return path, True

    def create_item(self, item, folder: Path, template: str, day: str | None = None) -> tuple[Path, bool]:
        """其他平台的内容（小红书 / 知乎 / B站）建笔记"""
        existing = self.find(item.id)
        if existing:
            return existing, False
        status = note.PENDING if item.media_urls else note.READY
        name = f"{day or date.today().isoformat()}_{note.safe_name(item.title)}_{item.id}.md"
        path = folder / name
        note.write_atomic(path, note.render_item(item, template, status))
        self.ids[item.id] = path
        return path, True

    def notes(self) -> list[note.NoteInfo]:
        out = []
        for vid, p in sorted(self.ids.items(), key=lambda kv: str(kv[1])):
            i = note.info(p)
            if i:
                out.append(i)
        return out
