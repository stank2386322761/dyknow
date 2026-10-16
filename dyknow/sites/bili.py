"""B站：视频搜索（按播放 / 点赞）、视频简介和音频（可转录）、热门。基本不需要登录。"""

from __future__ import annotations

import re
import time
from urllib.parse import quote

from . import HotItem, Item, Result, from_timestamp, parse_count, site_url, strip_html, walk

NAME = "bili"
WWW = "https://www.bilibili.com"
SEARCH = "https://search.bilibili.com"
NEEDS_LOGIN = False
LOGIN_URL = "https://passport.bilibili.com/login"
WATCH_SEARCH = ("/x/web-interface/wbi/search/type", "/x/web-interface/wbi/search/all", "/x/web-interface/search/")


def logged_in(b, cfg) -> bool | None:
    return bool(b.cookie_names(site_url(cfg, NAME, "www", WWW)).get("SESSDATA"))


def _duration_ms(v) -> int:
    m = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+)", str(v or "").strip())
    if not m:
        return 0
    h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return (h * 3600 + mi * 60 + s) * 1000


def parse_video(v: dict, www: str = WWW) -> Item | None:
    bvid = str(v.get("bvid") or "")
    if not bvid:
        return None
    stat = v.get("stat") or {}
    owner = v.get("owner") or {}
    return Item(
        platform=NAME, id=f"bili-{bvid}", title=strip_html(v.get("title")) or "（无标题）",
        url=f"{www}/video/{bvid}", author=str(v.get("author") or owner.get("name") or ""),
        published=from_timestamp(v.get("pubdate") or v.get("senddate") or v.get("ctime")),
        likes=parse_count(v.get("like") or stat.get("like")),
        collects=parse_count(v.get("favorites") or stat.get("favorite")),
        comments=parse_count(v.get("review") or stat.get("reply")),
        views=parse_count(v.get("play") or stat.get("view")),
        kind="视频", excerpt=strip_html(v.get("description") or v.get("desc") or "")[:200],
        duration_ms=_duration_ms(v.get("duration")) if isinstance(v.get("duration"), str) else int(v.get("duration") or 0) * 1000,
        tags=[t for t in str(v.get("tag") or "").split(",") if t][:10],
        extra={"bvid": bvid},
    )


def _extract(body: dict) -> list[dict]:
    return walk((body or {}).get("data") or {}, lambda d: bool(d.get("bvid")) and "title" in d)


def search(b, cfg, keyword: str, limit: int, days: int, log=print) -> Result:
    url = f"{site_url(cfg, NAME, 'search', SEARCH)}/video?keyword={quote(keyword)}&order=click"
    if days > 0:
        end = int(time.time())
        url += f"&pubtime_begin_s={end - days * 86400}&pubtime_end_s={end}"
    raw = b.page_collect(url, WATCH_SEARCH, _extract, max(limit * 2, 40),
                         key=lambda v: str(v.get("bvid") or ""), max_seconds=90)
    www = site_url(cfg, NAME, "www", WWW)
    items = [i for i in (parse_video(v, www) for v in raw) if i]
    if not items:
        if raw or b.captured:
            return Result(reason="parse", detail="收到了搜索数据但解析不出视频")
        return Result(reason="blocked", detail="搜索页没有返回视频")
    return Result(items=items)


_STATE_JS = """() => { try { return {
  video: JSON.parse(JSON.stringify((window.__INITIAL_STATE__ || {}).videoData || null)),
  play: JSON.parse(JSON.stringify(window.__playinfo__ || null)) }; } catch (e) { return null; } }"""


def detail(b, cfg, item: Item) -> None:
    b.goto(item.url)
    st = b.eval(_STATE_JS) or {}
    vd = st.get("video") or {}
    if vd:
        item.text = str(vd.get("desc") or "").strip()
        stat = vd.get("stat") or {}
        item.likes = parse_count(stat.get("like")) or item.likes
        item.collects = parse_count(stat.get("favorite")) or item.collects
        item.comments = parse_count(stat.get("reply")) or item.comments
        item.views = parse_count(stat.get("view")) or item.views
        item.published = from_timestamp(vd.get("pubdate")) or item.published
        item.duration_ms = int(vd.get("duration") or 0) * 1000 or item.duration_ms
    data = (st.get("play") or {}).get("data") or {}
    urls: list[str] = []
    audios = ((data.get("dash") or {}).get("audio")) or []
    audios = sorted([a for a in audios if isinstance(a, dict)], key=lambda a: int(a.get("bandwidth") or 0))
    for a in audios:  # 只要音频，码率低的先下（够转录用）
        for u in [a.get("baseUrl") or a.get("base_url")] + list(a.get("backupUrl") or a.get("backup_url") or []):
            if u and u not in urls:
                urls.append(u)
    for d in data.get("durl") or []:
        if isinstance(d, dict) and d.get("url"):
            urls.append(d["url"])
    item.media_urls = urls
    item.referer = WWW + "/"


def hot(b, cfg, limit: int, log=print) -> Result:
    www = site_url(cfg, NAME, "www", WWW)
    raw = b.page_collect(f"{www}/v/popular/all", ("/x/web-interface/popular",),
                         lambda body: [x for x in ((body.get("data") or {}).get("list") or []) if isinstance(x, dict)],
                         limit, rounds=4, idle_rounds=2, max_seconds=40, key=lambda x: str(x.get("bvid") or ""))
    out = []
    for i, x in enumerate(raw[:limit], 1):
        stat = x.get("stat") or {}
        heat = f"{parse_count(stat.get('view')) // 10000}万播放" if stat.get("view") else ""
        out.append(HotItem(NAME, i, strip_html(x.get("title")), f"{www}/video/{x.get('bvid')}", heat=heat,
                           label=str((x.get("owner") or {}).get("name") or "")))
    if not out:
        return Result(reason="blocked", detail="热门页面没有数据")
    return Result(hot=out)
