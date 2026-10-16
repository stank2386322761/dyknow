"""笔记读写。

一个视频 = 一个 Markdown 文件，进度只记在文件的 status 字段里。
程序负责的区块（文案、转写、热门评论）用 HTML 注释标记包起来，
重写时只替换标记之间的内容，AI 写的部分永远不会被覆盖。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .douyin import Comment, Video, clean_title, fmt_count, fmt_duration

# ── 状态 ──────────────────────────────────────
PENDING = "待转录"
TRANSCRIBED = "已转录"
ORGANIZED = "已整理"
FAILED = "转录失败"
IMAGE = "图文"
NO_SPEECH = "无语音"
READY = "待整理"    # 图文类内容（小红书笔记、知乎回答），正文已抓到
STATUSES = (PENDING, TRANSCRIBED, ORGANIZED, FAILED, IMAGE, NO_SPEECH, READY)
# 等 AI 整理的状态
TODO_STATUSES = (TRANSCRIBED, IMAGE, READY)

TEMPLATES_DIR = Path(__file__).parent / "templates"
_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f\x7f]')


# ── 模板 ──────────────────────────────────────


def load_template(name: str) -> str:
    """general / creator / 自定义模板文件路径"""
    name = (name or "general").strip()
    builtin = TEMPLATES_DIR / f"{name}.md"
    if builtin.is_file():
        return builtin.read_text(encoding="utf-8").strip()
    custom = Path(name).expanduser()
    if custom.is_file():
        return custom.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"找不到模板：{name}（可用 general、creator，或填一个 .md 文件路径）")


# ── frontmatter ───────────────────────────────


def _dump(value) -> str:
    # JSON 字符串同时也是合法 YAML，任何字符都不会把格式弄坏
    return json.dumps(value, ensure_ascii=False)


def _load(raw: str):
    raw = raw.strip()
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except ValueError:
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
            return raw[1:-1]
        return raw


def read_meta(text: str) -> dict:
    m = _FM_RE.match(text or "")
    if not m:
        return {}
    meta: dict = {}
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t#" or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        meta[key.strip()] = _load(raw)
    return meta


def set_meta(text: str, key: str, value) -> str:
    m = _FM_RE.match(text or "")
    line = f"{key}: {_dump(value)}"
    if not m:
        return f"---\n{line}\n---\n{text}"
    lines = m.group(1).splitlines()
    for i, existing in enumerate(lines):
        if existing.split(":", 1)[0].strip() == key and existing[:1] not in " \t":
            lines[i] = line
            break
    else:
        lines.append(line)
    return f"---\n" + "\n".join(lines) + "\n---\n" + text[m.end():]


def get_status(text: str) -> str:
    return str(read_meta(text).get("status") or "").strip()


def set_status(text: str, status: str) -> str:
    return set_meta(text, "status", status)


# ── 程序区块 ──────────────────────────────────


def _markers(name: str) -> tuple[str, str]:
    return f"<!-- dyknow:{name}:start -->", f"<!-- dyknow:{name}:end -->"


# 程序区块在笔记里的固定顺序
BLOCK_ORDER = ("desc", "frames", "transcript", "comments")


def replace_block(text: str, name: str, content: str) -> str:
    start, end = _markers(name)
    block = f"{start}\n{content.rstrip()}\n{end}" if content.strip() else f"{start}\n{end}"
    i = text.find(start)
    j = text.find(end, i + 1) if i >= 0 else -1
    if i >= 0 and j >= 0:
        return text[:i] + block + text[j + len(end):]
    # 区块不存在（比如旧笔记没有「画面」）：插到按顺序排在它后面的第一个区块前面
    if name in BLOCK_ORDER:
        for later in BLOCK_ORDER[BLOCK_ORDER.index(name) + 1:]:
            k = text.find(_markers(later)[0])
            if k >= 0:
                return text[:k] + block + "\n\n" + text[k:]
    return text.rstrip() + "\n\n" + block + "\n"


def get_block(text: str, name: str) -> str:
    start, end = _markers(name)
    i = text.find(start)
    j = text.find(end, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return ""
    return text[i + len(start):j].strip()


def _callout(title: str, lines: list[str], kind: str = "note", gap: bool = True) -> str:
    body = ("\n>\n" if gap else "\n").join(f"> {ln}" for ln in lines) if lines else "> （空）"
    return f"> [!{kind}]- {title}\n{body}"


def fmt_ts(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def transcript_block(paragraphs: list[tuple[float | None, str]], extra: str = "") -> str:
    lines = []
    for start, text in paragraphs:
        text = text.strip()
        if not text:
            continue
        lines.append(f"**[{fmt_ts(start)}]** {text}" if start is not None else text)
    title = "转写" + (f"（{extra}）" if extra else "")
    return _callout(title, lines)


def comments_block(comments: list[Comment]) -> str:
    if not comments:
        return ""
    lines = [f"- 👍{fmt_count(c.likes)} 「{c.text}」" for c in comments]
    return _callout(f"热门评论（抓取于 {date.today().isoformat()}）", lines, gap=False)


def frames_block(note_path: Path, frames: list[tuple[float | None, Path]], kind: str = "video") -> str:
    """画面截图（给能看图的 AI 和人看）。图片路径相对于笔记文件"""
    if not frames:
        return ""
    lines = []
    for t, img in frames:
        rel = os.path.relpath(img, Path(note_path).parent).replace(os.sep, "/")
        label = fmt_ts(t) if t is not None else img.stem
        lines.append(f"![{label}|240]({rel})" + (f" {fmt_ts(t)}" if t is not None else ""))  # |240 = Obsidian 里显示宽度
    title = f"图片（{len(frames)} 张）" if kind == "image" else f"画面（{len(frames)} 张，画面变化时截取）"
    return _callout(title, lines)


def frames_dir(note_path: Path, vid: str) -> Path:
    return Path(note_path).parent / "附件" / vid


def desc_block(video: Video) -> str:
    desc = (video.desc or "").strip()
    rest = re.sub(r"\s+", " ", re.sub(r"#\s*[^#\s@]+", "", desc)).strip(" ，,。")
    if not desc or rest == video.title or not rest:
        return ""  # 文案就是标题加话题，不重复显示
    return _callout("视频文案", [ln for ln in desc.splitlines() if ln.strip()], kind="quote", gap=False)


# ── 新建笔记 ──────────────────────────────────


def safe_name(text: str, limit: int = 30) -> str:
    s = _BAD_NAME.sub(" ", text or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip(" .") or "无标题"


def file_name(video: Video, day: str) -> str:
    return f"{day}_{safe_name(video.title)}_{video.id}.md"


def render(video: Video, template: str, status: str, comments: list[Comment] | None = None) -> str:
    meta = [
        ("id", video.id),
        ("url", video.url),
        ("author", video.author),
        ("published", video.published),
        ("tags", video.tags),
        ("status", status),
    ]
    head = "---\n" + "\n".join(f"{k}: {_dump(v)}" for k, v in meta) + "\n---\n"
    bits = [video.author or "未知作者"]
    if video.duration_ms and not video.is_image:
        bits.append(fmt_duration(video.duration_ms))
    if video.published:
        bits.append(video.published)
    bits.append(f"👍{fmt_count(video.likes)}")
    if video.comments:
        bits.append(f"💬{fmt_count(video.comments)}")
    if video.collects:
        bits.append(f"⭐{fmt_count(video.collects)}")
    bits.append(f"[原视频]({video.url})")
    title = video.title.replace("\n", " ")
    parts = [
        head,
        f"# {title}\n",
        "> " + " · ".join(bits) + "\n",
        template.strip() + "\n",
    ]
    text = "\n".join(parts)
    # 区块顺序固定：文案 → 画面 → 转写 → 热门评论
    text = replace_block(text, "desc", desc_block(video))
    text = replace_block(text, "frames", "")
    if status == IMAGE:
        text = replace_block(text, "transcript", "> [!note]- 转写\n> 图文作品，没有语音，内容见上方文案。")
    elif status == PENDING:
        text = replace_block(text, "transcript", "> [!note]- 转写\n> 待转录")
    else:
        text = replace_block(text, "transcript", "")
    text = replace_block(text, "comments", comments_block(comments or []))
    return text


def render_item(item, template: str, status: str) -> str:
    """其他平台（小红书 / 知乎 / B站）的笔记，格式和抖音笔记一致"""
    meta = [
        ("id", item.id), ("url", item.url), ("platform", item.platform_name), ("author", item.author),
        ("published", item.published), ("tags", item.tags), ("status", status),
    ]
    head = "---\n" + "\n".join(f"{k}: {_dump(v)}" for k, v in meta) + "\n---\n"
    bits = [item.platform_name, item.kind or "内容", item.author or "未知作者"]
    if item.duration_ms:
        bits.append(fmt_duration(item.duration_ms))
    if item.published:
        bits.append(item.published)
    if item.views:
        bits.append(f"▶{fmt_count(item.views)}")
    bits.append(f"👍{fmt_count(item.likes)}")
    if item.collects:
        bits.append(f"⭐{fmt_count(item.collects)}")
    if item.comments:
        bits.append(f"💬{fmt_count(item.comments)}")
    bits.append(f"[原文]({item.url})")
    title = item.title.replace("\n", " ")[:80]
    text = "\n".join([head, f"# {title}\n", "> " + " · ".join(bits) + "\n", template.strip() + "\n"])
    body = (item.text or item.excerpt or "").strip()
    label = "简介" if item.kind == "视频" else "原文"
    text = replace_block(text, "desc", _callout(label, body.splitlines(), kind="quote", gap=False) if body else "")
    text = replace_block(text, "frames", "")
    text = replace_block(text, "transcript", "> [!note]- 转写\n> 待转录" if status == PENDING else "")
    text = replace_block(text, "comments", "")
    return text


# ── 文件 ──────────────────────────────────────


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # 同步盘 / 杀毒软件短暂锁文件
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            import time
            time.sleep(1)


def read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


@dataclass
class NoteInfo:
    path: Path
    id: str
    status: str
    title: str
    summary: str


def first_ai_line(text: str) -> str:
    """AI 区第一个小标题下写的第一行（用于索引里的"一句话"）"""
    body = _FM_RE.sub("", text, count=1)
    cut = body.find("<!-- dyknow:")
    if cut >= 0:
        body = body[:cut]
    in_section = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("## "):
            if in_section:
                return ""
            in_section = True
            continue
        if not in_section or not s or s.startswith("<!--"):
            continue
        s = re.sub(r"<!--.*?-->", "", s).strip().lstrip("-*• ").strip()
        if s:
            return s[:120]
    return ""


def info(path: Path) -> NoteInfo | None:
    try:
        text = read(path)
    except (OSError, UnicodeDecodeError):
        return None
    meta = read_meta(text)
    nid = str(meta.get("id") or "")
    if not nid:
        return None
    title = ""
    for line in _FM_RE.sub("", text, count=1).splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return NoteInfo(path=path, id=nid, status=str(meta.get("status") or ""),
                    title=title or clean_title(path.stem), summary=first_ai_line(text))
