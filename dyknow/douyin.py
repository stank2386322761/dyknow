"""抖音数据结构解析（纯函数，不联网）。

接口返回的作品对象（aweme）字段很多，这里只取知识库需要的，
并把图文作品和视频作品区分开：图文作品的"视频地址"其实是图片轮播 + 背景音乐，
拿去转录只会得到歌词。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

IMAGE_AWEME_TYPES = {2, 68, 150}

_ID_PATTERNS = [
    re.compile(r"douyin\.com/(?:video|note|slides)/(\d{15,25})"),
    re.compile(r"iesdouyin\.com/share/(?:video|note|slides)/(\d{15,25})"),
    re.compile(r"[?&](?:modal_id|aweme_id|vid)=(\d{15,25})"),
]
_SHORT_RE = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_\-]+/?")
_URL_RE = re.compile(r"https?://[^\s，。！？　\"'<>]+")
_USER_RE = re.compile(r"/user/([A-Za-z0-9_\-.]+)")
_HASHTAG_RE = re.compile(r"#\s*([^#\s@]+)")


@dataclass
class Video:
    id: str
    desc: str = ""
    author: str = ""
    author_sec_uid: str = ""
    published: str = ""          # YYYY-MM-DD
    duration_ms: int = 0
    likes: int = 0
    comments: int = 0
    collects: int = 0
    shares: int = 0
    is_image: bool = False
    tags: list[str] = field(default_factory=list)
    media_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)   # 图文作品的图片
    referer: str = ""                                      # 下载时用的来源页（其他平台用）

    @property
    def url(self) -> str:
        return f"https://www.douyin.com/video/{self.id}"

    @property
    def title(self) -> str:
        """去掉话题标签后的第一行文案，作为标题"""
        return clean_title(self.desc) or f"抖音作品 {self.id}"


@dataclass
class Comment:
    text: str
    likes: int = 0
    replies: int = 0


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


TITLE_MAX = 50


def clean_title(desc: str) -> str:
    """文案第一行去掉话题标签；太长时在句号处截断（完整文案放在笔记的「视频文案」里）"""
    text = _HASHTAG_RE.sub("", desc or "")
    text = re.sub(r"@\S+", "", text)
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" ，,。")
        if not line:
            continue
        if len(line) <= TITLE_MAX:
            return line
        m = re.search(r"[。！？!?]", line[10:TITLE_MAX + 1])
        if m:
            return line[:10 + m.start() + 1].rstrip("。")
        return line[:TITLE_MAX].rstrip(" ，,") + "…"
    return ""


def extract_tags(aweme: dict, desc: str) -> list[str]:
    tags: list[str] = []
    for extra in aweme.get("text_extra") or []:
        if isinstance(extra, dict):
            name = str(extra.get("hashtag_name") or "").strip()
            if name and name not in tags:
                tags.append(name)
    if not tags:
        for name in _HASHTAG_RE.findall(desc or ""):
            name = name.strip()
            if name and name not in tags:
                tags.append(name)
    return tags[:10]


def _media_urls(aweme: dict) -> list[str]:
    """候选下载地址：码率最低的完整 mp4 排前面（越小下载越快）。

    bit_rate 里 format=dash 的是只有画面、没有声音的分轨文件，不能用来转录，排除掉。
    下载后还会再检查一次音轨（见 media.probe），没有声音就换下一个地址。"""
    video = aweme.get("video") or {}
    urls: list[str] = []

    def add(addr):
        if isinstance(addr, dict):
            for u in addr.get("url_list") or []:
                if isinstance(u, str) and u.startswith("http") and u not in urls:
                    urls.append(u)

    rates = [r for r in (video.get("bit_rate") or []) if isinstance(r, dict)
             and str(r.get("format") or "mp4").lower() != "dash"]
    rates.sort(key=lambda r: _int(r.get("bit_rate")) or 10**12)
    for r in rates:
        add(r.get("play_addr"))
    add(video.get("play_addr"))
    add(video.get("play_addr_h264"))
    add(video.get("download_addr"))
    return urls


def _image_urls(aweme: dict) -> list[str]:
    out = []
    for img in aweme.get("images") or []:
        if not isinstance(img, dict):
            continue
        lst = [u for u in (img.get("url_list") or img.get("download_url_list") or [])
               if isinstance(u, str) and u.startswith("http")]
        # 优先 jpeg，其次 webp
        lst.sort(key=lambda u: 0 if ".jpeg" in u or ".jpg" in u else 1)
        if lst:
            out.append(lst[0])
    return out


def parse_aweme(aweme: dict) -> Video | None:
    if not isinstance(aweme, dict):
        return None
    aid = str(aweme.get("aweme_id") or "").strip()
    if not re.fullmatch(r"\d{6,25}", aid):
        return None
    desc = str(aweme.get("desc") or "").strip()
    author = aweme.get("author") or {}
    stats = aweme.get("statistics") or {}
    video = aweme.get("video") or {}
    ct = _int(aweme.get("create_time"))
    published = datetime.fromtimestamp(ct).strftime("%Y-%m-%d") if ct > 0 else ""
    is_image = (
        _int(aweme.get("aweme_type")) in IMAGE_AWEME_TYPES
        or bool(aweme.get("images"))
        or bool(aweme.get("image_post_info"))
    )
    return Video(
        id=aid,
        desc=desc,
        author=str(author.get("nickname") or "").strip(),
        author_sec_uid=str(author.get("sec_uid") or "").strip(),
        published=published,
        duration_ms=_int(video.get("duration") or aweme.get("duration")),
        likes=_int(stats.get("digg_count")),
        comments=_int(stats.get("comment_count")),
        collects=_int(stats.get("collect_count")),
        shares=_int(stats.get("share_count")),
        is_image=is_image,
        tags=extract_tags(aweme, desc),
        media_urls=[] if is_image else _media_urls(aweme),
        image_urls=_image_urls(aweme) if is_image else [],
    )


def parse_comment(raw: dict) -> Comment | None:
    if not isinstance(raw, dict):
        return None
    text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()
    if not text:
        return None
    return Comment(text=text, likes=_int(raw.get("digg_count")), replies=_int(raw.get("reply_comment_total")))


def awemes_from_search(body: dict) -> list[dict]:
    """搜索接口的结构经常变：data 里可能是 {aweme_info: {...}} 或直接是作品"""
    out: list[dict] = []
    for entry in (body or {}).get("data") or []:
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("aweme_info"), dict):
            out.append(entry["aweme_info"])
        elif entry.get("aweme_id"):
            out.append(entry)
    return out


# ── 链接识别 ──────────────────────────────────


def video_id_from_url(url: str) -> str:
    for pat in _ID_PATTERNS:
        m = pat.search(url or "")
        if m:
            return m.group(1)
    return ""


def is_shipin_link(url: str) -> bool:
    """专题页编号不是作品 ID；必须从页面的主视频数据解析。"""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    return (parsed.scheme in ("http", "https")
            and (host == "douyin.com" or host.endswith(".douyin.com"))
            and re.fullmatch(r"/shipin/\d{15,25}/?", parsed.path) is not None)


def shipin_video_id_from_html(html: str, source_url: str) -> str:
    """只读取专题页明确的 shipin.mainVideo，不把相关推荐或页面编号当作品。"""
    if not is_shipin_link(source_url):
        return ""
    page_id = urlparse(source_url).path.rstrip("/").rsplit("/", 1)[-1]

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.in_script = False
            self.parts: list[str] = []
            self.scripts: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.in_script = True
                self.parts = []

        def handle_data(self, data):
            if self.in_script:
                self.parts.append(data)

        def handle_endtag(self, tag):
            if tag == "script" and self.in_script:
                self.scripts.append("".join(self.parts))
                self.in_script = False

    parser = Scripts()
    parser.feed(html or "")
    decoder = json.JSONDecoder()
    candidates: set[str] = set()

    def find(value, depth=0):
        if depth > 30:
            return
        if isinstance(value, dict):
            if "shipinID" in value and str(value["shipinID"]) != page_id:
                return
            shipin = value.get("shipin")
            if isinstance(shipin, dict):
                main = shipin.get("mainVideo")
                if isinstance(main, dict):
                    ids = {str(main[k]) for k in ("awemeId", "aweme_id", "itemId")
                           if main.get(k) is not None}
                    if main.get("videoUrl"):
                        vid = video_id_from_url(str(main["videoUrl"]))
                        if vid:
                            ids.add(vid)
                    if len(ids) == 1 and re.fullmatch(r"\d{15,25}", next(iter(ids))):
                        candidates.update(ids)
            for child in value.values():
                find(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                find(child, depth + 1)

    def decode_payload(text):
        text = text.strip()
        if text[:3].lower() in ("%7b", "%5b"):
            text = unquote(text)
        text = re.sub(r"^[0-9a-fA-F]+:", "", text, count=1)
        try:
            value, _ = decoder.raw_decode(text)
        except (ValueError, RecursionError):
            return
        find(value)

    for script in parser.scripts:
        decode_payload(script)
        # React 服务端流：只解码 JSON 数组和字符串，不执行页面脚本。
        for match in re.finditer(r"(?:self|window)\.__pace_f\.push\(\s*", script):
            try:
                chunk, _ = decoder.raw_decode(script, match.end())
            except (ValueError, RecursionError):
                continue
            if isinstance(chunk, list) and len(chunk) > 1 and isinstance(chunk[1], str):
                decode_payload(chunk[1])
    return next(iter(candidates)) if len(candidates) == 1 else ""


def sec_uid_from_url(url: str) -> str:
    m = _USER_RE.search(url or "")
    if m and m.group(1) != "self":
        return m.group(1)
    qs = parse_qs(urlparse(url or "").query)
    return (qs.get("sec_uid") or qs.get("sec_user_id") or [""])[0]


def find_links(text: str) -> list[str]:
    """从分享文案里找出所有抖音链接（保持顺序、去重）"""
    out: list[str] = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:)）】")
        host = (urlparse(url).hostname or "").lower()
        if host == "douyin.com" or host.endswith(".douyin.com") or host.endswith("iesdouyin.com"):
            if url not in out:
                out.append(url)
    # 纯数字 ID 也接受
    for m in re.finditer(r"(?<!\d)(\d{19})(?!\d)", text or ""):
        if not any(m.group(1) in u for u in out):
            out.append(m.group(1))
    return out


def is_short_link(url: str) -> bool:
    return bool(_SHORT_RE.match(url or ""))


def fmt_count(n: int) -> str:
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿".replace(".0亿", "亿")
    if n >= 10_000:
        return f"{n / 10_000:.1f}万".replace(".0万", "万")
    return str(n)


def fmt_duration(ms: int) -> str:
    if ms <= 0:
        return ""
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"
