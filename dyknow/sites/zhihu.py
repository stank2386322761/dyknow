"""知乎：内容搜索（回答 / 文章，按赞同排序）、回答和文章全文、热榜。搜索需要登录，热榜不需要。"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from . import HotItem, Item, Result, from_timestamp, html_to_text, parse_count, site_url, strip_html, walk

NAME = "zhihu"
HOME = "https://www.zhihu.com"
ZHUANLAN = "https://zhuanlan.zhihu.com"
API_SEARCH = "/api/v4/search_v3"
NEEDS_LOGIN = True
LOGIN_URL = HOME + "/signin"


def _base(cfg) -> str:
    return site_url(cfg, NAME, "www", HOME)


def logged_in(b, cfg) -> bool | None:
    return bool(b.cookie_names(_base(cfg)).get("z_c0"))


def _interval(days: int) -> str:
    for limit, name in ((1, "a_day"), (7, "a_week"), (31, "a_month"), (92, "three_months"),
                        (183, "half_a_year"), (366, "a_year")):
        if 0 < days <= limit:
            return name
    return ""


def parse_object(obj: dict, base: str = HOME, zhuanlan: str = ZHUANLAN) -> Item | None:
    if not isinstance(obj, dict):
        return None
    typ = str(obj.get("type") or "")
    oid = str(obj.get("id") or "")
    if not oid or typ not in ("answer", "article", "zvideo"):
        return None
    question = obj.get("question") or {}
    title = strip_html(obj.get("title") or question.get("name") or question.get("title") or "")
    author = (obj.get("author") or {}).get("name") or ""
    if typ == "answer":
        qid = str(question.get("id") or "")
        url = f"{base}/question/{qid}/answer/{oid}" if qid else f"{base}/answer/{oid}"
        iid, kind = f"zh-a{oid}", "回答"
    elif typ == "article":
        url, iid, kind = f"{zhuanlan}/p/{oid}", f"zh-p{oid}", "文章"
    else:
        url, iid, kind = f"{base}/zvideo/{oid}", f"zh-v{oid}", "视频"
    return Item(
        platform=NAME, id=iid, title=title or "（无标题）", url=url, author=str(author),
        published=from_timestamp(obj.get("created_time") or obj.get("created") or obj.get("updated_time")),
        likes=parse_count(obj.get("voteup_count") or obj.get("vote_count")),
        comments=parse_count(obj.get("comment_count")),
        collects=parse_count(obj.get("favorite_count") or obj.get("favlists_count")),
        kind=kind, excerpt=strip_html(obj.get("excerpt") or obj.get("description") or "")[:200],
        extra={"type": typ, "oid": oid, "qid": str(question.get("id") or "")},
    )


def _extract(body: dict) -> list[dict]:
    out = []
    for e in (body or {}).get("data") or []:
        if not isinstance(e, dict):
            continue
        obj = e.get("object") or e
        if isinstance(obj, dict) and obj.get("type") in ("answer", "article", "zvideo"):
            if e.get("highlight") and not obj.get("title"):
                obj = {**obj, "title": (e.get("highlight") or {}).get("title", "")}
            out.append(obj)
    return out


def search(b, cfg, keyword: str, limit: int, days: int, log=print) -> Result:
    base = _base(cfg)
    url = f"{base}/search?q={quote(keyword)}&type=content&sort=upvoted_count"
    if _interval(days):
        url += f"&time_interval={_interval(days)}"
    raw = b.page_collect(url, (API_SEARCH,), _extract, max(limit * 2, 40),
                         key=lambda o: f"{o.get('type')}{o.get('id')}", max_seconds=90)
    captured = b.captured
    items = [i for i in (parse_object(o, base, site_url(cfg, NAME, "zhuanlan", ZHUANLAN)) for o in raw) if i]
    if not items:
        if not logged_in(b, cfg):
            return Result(reason="login")
        if raw or captured:
            return Result(reason="parse", detail="收到了搜索数据但解析不出内容")
        return Result(reason="blocked", detail="搜索页没有返回内容")
    return Result(items=items)


def _initial_data(b) -> dict | None:
    txt = b.eval("() => { const e = document.getElementById('js-initialData'); return e ? e.textContent : null; }")
    if not txt and b.page is not None:
        m = re.search(r'<script id="js-initialData"[^>]*>(.*?)</script>', b.page.content(), re.DOTALL)
        txt = m.group(1) if m else None
    try:
        return json.loads(txt) if txt else None
    except ValueError:
        return None


def detail(b, cfg, item: Item) -> None:
    b.goto(item.url)
    data = _initial_data(b)
    if not data:
        return
    entities = ((data.get("initialState") or {}).get("entities") or {})
    oid = item.extra.get("oid", "")
    typ = item.extra.get("type", "")
    bucket = entities.get("answers" if typ == "answer" else "articles" if typ == "article" else "zvideos") or {}
    ent = bucket.get(oid) or bucket.get(str(oid)) or {}
    content = ent.get("content") or ent.get("description") or ""
    if content:
        item.text = html_to_text(content)
    item.likes = parse_count(ent.get("voteupCount") or ent.get("voteup_count")) or item.likes
    item.comments = parse_count(ent.get("commentCount") or ent.get("comment_count")) or item.comments
    item.published = from_timestamp(ent.get("createdTime") or ent.get("created") or ent.get("created_time")) or item.published


def hot(b, cfg, limit: int, log=print) -> Result:
    """知乎热榜（billboard 页面不需要登录）"""
    base = _base(cfg)
    b.goto(base + "/billboard")
    data = _initial_data(b) or {}
    rows = walk(data, lambda d: isinstance(d.get("target"), dict) and "titleArea" in d["target"])
    out = []
    for i, r in enumerate(rows[:limit], 1):
        t = r["target"]
        title = strip_html((t.get("titleArea") or {}).get("text"))
        link = (t.get("link") or {}).get("url") or ""
        heat = strip_html((t.get("metricsArea") or {}).get("text"))
        if title:
            out.append(HotItem(NAME, i, title, link, heat=heat))
    if not out:
        # 兜底：直接读页面上的标题
        titles = b.eval("() => Array.from(document.querySelectorAll('.HotList-itemTitle')).map(e => e.innerText)") or []
        out = [HotItem(NAME, i, t.strip(), "", "") for i, t in enumerate(titles[:limit], 1) if t.strip()]
    if not out:
        return Result(reason="blocked", detail="热榜页面没有数据")
    return Result(hot=out)
