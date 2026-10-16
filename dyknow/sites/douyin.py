"""抖音：热榜，以及按点赞排序的搜索（复用 DyKnow 原来的抖音抓取）。"""

from __future__ import annotations

from urllib.parse import quote

from ..douyin import Video, parse_aweme
from . import HotItem, Item, Result

NAME = "douyin"
NEEDS_LOGIN = False
API_HOT = "/aweme/v1/web/hot/search/list/"


def logged_in(b, cfg) -> bool | None:
    return b.logged_in()


def to_item(v: Video) -> Item:
    return Item(
        platform=NAME, id=v.id, title=v.title, url=v.url, author=v.author, published=v.published,
        likes=v.likes, collects=v.collects, comments=v.comments, shares=v.shares,
        kind="图文" if v.is_image else "视频", excerpt=v.desc[:200], tags=v.tags,
        media_urls=v.media_urls, duration_ms=v.duration_ms, extra={"video": v},
    )


def search(b, cfg, keyword: str, limit: int, days: int, log=print) -> Result:
    res = b.search(keyword, max(limit * 2, 30), by_likes=True, days=days)
    if res.reason:
        return Result(reason=res.reason, detail=res.detail)
    items = [to_item(v) for v in (parse_aweme(a) for a in res.items) if v]
    return Result(items=items)


def detail(b, cfg, item: Item) -> None:
    return None  # 搜索结果里已经有全部信息，转录走原来的流程


def hot(b, cfg, limit: int, log=print) -> Result:
    from ..browser import ApiFail

    def rows_of(body):
        return [w for w in ((body.get("data") or {}).get("word_list") or []) if isinstance(w, dict)]

    try:
        rows = rows_of(b.api(API_HOT, {"detail_list": "1", "source": "6", "board_type": "0", "board_sub_type": ""}))
    except ApiFail:
        rows = []
    if not rows:
        rows = b.page_collect(b.base + "/hot", (API_HOT,), rows_of, limit, rounds=3, idle_rounds=2,
                              max_seconds=40, key=lambda w: str(w.get("word") or ""))
    out = []
    for w in rows[:limit]:
        word = str(w.get("word") or "").strip()
        if not word:
            continue
        sid = w.get("sentence_id")
        url = f"{b.base}/hot/{sid}" if sid else f"{b.base}/search/{quote(word)}"
        heat = w.get("hot_value")
        label = {1: "新", 3: "热", 5: "独家", 8: "首发"}.get(w.get("label"), "") if isinstance(w.get("label"), int) else ""
        out.append(HotItem(NAME, len(out) + 1, word, url,
                           heat=f"{int(heat) // 10000}万" if isinstance(heat, (int, float)) and heat >= 10000 else str(heat or ""),
                           label=label))
    if not out:
        return Result(reason="blocked", detail="没拿到抖音热榜")
    return Result(hot=out)
