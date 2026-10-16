"""其他平台（小红书、知乎、B站、微博、抖音热榜）：找选题、找对标用。

每个平台一个模块，提供同样的几个函数：
    search(b, cfg, keyword, limit, days, log) -> Result     关键词搜索（返回 Item）
    hot(b, cfg, limit, log) -> Result                        热榜（返回 HotItem）
    detail(b, cfg, item) -> None                             补全正文 / 图片 / 音视频地址（存笔记前用）
    logged_in(b, cfg) -> bool | None                         None = 判断不了

取数方式和抖音一样：打开网页，记录网页自己加载的数据。不破解签名，网页能看到的才能抓到。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

PLATFORM_NAMES = {"douyin": "抖音", "xhs": "小红书", "zhihu": "知乎", "bili": "B站", "weibo": "微博"}
ALIASES = {
    "抖音": "douyin", "dy": "douyin", "douyin": "douyin",
    "小红书": "xhs", "红书": "xhs", "xhs": "xhs", "xiaohongshu": "xhs",
    "知乎": "zhihu", "zh": "zhihu", "zhihu": "zhihu",
    "b站": "bili", "B站": "bili", "bilibili": "bili", "bili": "bili", "哔哩哔哩": "bili",
    "微博": "weibo", "wb": "weibo", "weibo": "weibo",
}


@dataclass
class Item:
    """一条内容（笔记 / 回答 / 文章 / 视频）"""
    platform: str
    id: str                 # 带平台前缀，如 xhs-64f0…、zh-a123、bili-BV1…（抖音保持纯数字）
    title: str
    url: str
    author: str = ""
    published: str = ""     # YYYY-MM-DD，拿不到为空
    likes: int = 0
    collects: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
    kind: str = ""          # 笔记 / 视频 / 回答 / 文章
    excerpt: str = ""
    text: str = ""          # 正文全文（detail 之后才有）
    tags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    media_urls: list[str] = field(default_factory=list)  # 需要转录的音视频地址
    referer: str = ""
    duration_ms: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def platform_name(self) -> str:
        return PLATFORM_NAMES.get(self.platform, self.platform)

    @property
    def score(self) -> int:
        """排序用的热度：优先点赞，没有点赞数用收藏，再没有用播放"""
        return self.likes or self.collects or self.views // 50


@dataclass
class HotItem:
    platform: str
    rank: int
    title: str
    url: str = ""
    heat: str = ""
    label: str = ""


@dataclass
class Result:
    items: list[Item] = field(default_factory=list)
    hot: list[HotItem] = field(default_factory=list)
    reason: str = ""        # 空 = 成功；login / blocked / parse
    detail: str = ""
    note: str = ""          # 给用户看的补充说明（比如"没点到排序按钮，按默认排序抓的"）


def platforms(spec: str, default: list[str]) -> list[str]:
    if not spec:
        return default
    out = []
    for part in re.split(r"[,，\s]+", spec):
        key = ALIASES.get(part.strip()) or ALIASES.get(part.strip().lower())
        if key and key not in out:
            out.append(key)
    return out


def module(name: str):
    from importlib import import_module
    return import_module(f".{name}", __name__)


def site_url(cfg, platform: str, key: str, default: str) -> str:
    """站点地址（测试时可以替换成本地假服务器）"""
    return (getattr(cfg, "site_urls", {}) or {}).get(f"{platform}.{key}", default).rstrip("/")


# ── 文本处理 ──────────────────────────────────


def strip_html(s) -> str:
    s = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def html_to_text(s) -> str:
    s = str(s or "")
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|h\d|li|blockquote|figure)>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def parse_count(v) -> int:
    """'1.2万' / '10w+' / '3,456' / 789 → 整数"""
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v or "").strip().lower().replace(",", "").replace("+", "")
    m = re.match(r"([\d.]+)\s*([万wk千亿]?)", s)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    mult = {"万": 1e4, "w": 1e4, "k": 1e3, "千": 1e3, "亿": 1e8}.get(m.group(2), 1)
    return int(n * mult)


def from_timestamp(v) -> str:
    try:
        t = float(v)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    if t > 1e12:  # 毫秒
        t /= 1000
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d")


def parse_cn_time(text: str, today: date | None = None) -> str:
    """'刚刚' '3分钟前' '5小时前' '昨天 12:00' '3天前' '10-01' '2024-10-01' → YYYY-MM-DD"""
    today = today or date.today()
    s = str(text or "").strip()
    if not s:
        return ""
    if re.search(r"刚刚|分钟前|小时前|今天", s):
        return today.isoformat()
    if "前天" in s:
        return (today - timedelta(days=2)).isoformat()
    if "昨天" in s:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*天前", s)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s*(?:周|星期)前", s)
    if m:
        return (today - timedelta(weeks=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s*个?月前", s)
    if m:
        return (today - timedelta(days=30 * int(m.group(1)))).isoformat()
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(?<!\d)(\d{1,2})[-/月](\d{1,2})(?!\d)", s)
    if m:
        mon, day = int(m.group(1)), int(m.group(2))
        year = today.year if (mon, day) <= (today.month, today.day) else today.year - 1
        try:
            return date(year, mon, day).isoformat()
        except ValueError:
            return ""
    return ""


def within_days(published: str, days: int, today: date | None = None) -> bool | None:
    """在最近 days 天内？拿不到发布时间返回 None（不确定）"""
    if days <= 0:
        return True
    if not published:
        return None
    try:
        d = date.fromisoformat(published)
    except ValueError:
        return None
    return (today or date.today()) - d <= timedelta(days=days)


def walk(node, pred, limit: int = 2000):
    """在任意 JSON 里找出所有满足条件的字典（接口结构变了也能尽量找到）"""
    out, stack = [], [node]
    while stack and len(out) < limit:
        n = stack.pop()
        if isinstance(n, dict):
            if pred(n):
                out.append(n)
                continue
            stack.extend(reversed(list(n.values())))
        elif isinstance(n, list):
            stack.extend(reversed(n))
    return out
