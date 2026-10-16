"""小红书：关键词搜索（按点赞排序）、笔记正文和图片、热搜词。需要登录。"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from . import HotItem, Item, Result, parse_cn_time, parse_count, from_timestamp, site_url, walk

NAME = "xhs"
HOME = "https://www.xiaohongshu.com"
API_SEARCH = "/api/sns/web/v1/search/notes"
API_ME = "/api/sns/web/v2/user/me"
API_HOT = "/api/sns/web/v1/search/hot_list"
NEEDS_LOGIN = True
LOGIN_URL = HOME + "/explore"


def _base(cfg) -> str:
    return site_url(cfg, NAME, "www", HOME)


def logged_in(b, cfg) -> bool | None:
    """打开首页，看网页自己请求的"我的信息"接口：guest=true 就是没登录"""
    got = b.page_collect(_base(cfg) + "/explore", (API_ME,),
                         lambda body: [body.get("data") or {}], 1, rounds=3, idle_rounds=2,
                         max_seconds=20, key=lambda d: "me")
    if got:
        return not bool(got[0].get("guest", True))
    return None


def _days_label(days: int) -> list[str]:
    if days <= 0:
        return []
    if days <= 1:
        return ["一天内"]
    if days <= 7:
        return ["一周内"]
    return ["半年内"]


def parse_search_entry(e: dict, base: str = HOME) -> Item | None:
    card = e.get("note_card") or e.get("noteCard")
    if not isinstance(card, dict):
        return None
    nid = str(e.get("id") or card.get("note_id") or card.get("noteId") or "")
    if not nid:
        return None
    tok = str(e.get("xsec_token") or card.get("xsec_token") or "")
    user = card.get("user") or {}
    ii = card.get("interact_info") or card.get("interactInfo") or {}
    pub = ""
    for t in card.get("corner_tag_info") or []:
        if isinstance(t, dict) and t.get("type") == "publish_time":
            pub = parse_cn_time(t.get("text"))
    url = f"{base}/explore/{nid}" + (f"?xsec_token={quote(tok)}&xsec_source=pc_search" if tok else "")
    cover = (card.get("cover") or {}).get("url_default") or (card.get("cover") or {}).get("url") or ""
    return Item(
        platform=NAME, id=f"xhs-{nid}",
        title=str(card.get("display_title") or card.get("title") or "").strip() or "（无标题）",
        url=url, author=str(user.get("nickname") or user.get("nick_name") or ""),
        published=pub,
        likes=parse_count(ii.get("liked_count") or ii.get("likedCount")),
        collects=parse_count(ii.get("collected_count") or ii.get("collectedCount")),
        comments=parse_count(ii.get("comment_count") or ii.get("commentCount")),
        shares=parse_count(ii.get("shared_count") or ii.get("shareCount")),
        kind="视频" if card.get("type") == "video" else "笔记",
        images=[cover] if cover else [],
        extra={"note_id": nid, "xsec_token": tok},
    )


def _extract(body: dict) -> list[dict]:
    items = ((body or {}).get("data") or {}).get("items") or []
    return [e for e in items if isinstance(e, dict)]


def search(b, cfg, keyword: str, limit: int, days: int, log=print) -> Result:
    base = _base(cfg)
    url = f"{base}/search_result?keyword={quote(keyword)}&source=web_search_result_notes"
    notes: list[str] = []

    def filters():
        # 网页上的「筛选」：排序选最多点赞，时间选一周内/半年内。点不到就按默认排序抓，回来自己排
        b.click_text(["筛选"])
        picked = b.click_text(["最多点赞", "最热"])
        when = b.click_text(_days_label(days)) if days else ""
        if not picked:
            notes.append("没点到「最多点赞」，按默认排序抓取后自己按点赞排序")
        if days and not when:
            notes.append("没点到时间筛选，按发布时间自己过滤")

    raw = b.page_collect(url, (API_SEARCH,), _extract, max(limit * 2, 40),
                         key=lambda e: str(e.get("id") or ""), after_open=filters, max_seconds=90)
    captured = b.captured
    items = [i for i in (parse_search_entry(e, base) for e in raw) if i]
    if not items:
        if logged_in(b, cfg) is False:
            return Result(reason="login")
        if raw or captured:
            return Result(reason="parse", detail="收到了搜索数据但解析不出笔记")
        return Result(reason="blocked", detail="搜索页没有返回笔记（可能需要验证）")
    return Result(items=items, note="；".join(notes))


_STATE_JS = """() => { try {
  const s = window.__INITIAL_STATE__; if (!s || !s.note) return null;
  const m = s.note.noteDetailMap; const v = (m && (m._rawValue || m._value)) || m;
  return JSON.parse(JSON.stringify(v));
} catch (e) { return null; } }"""


def _state_from_html(html: str):
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(re.sub(r"\bundefined\b", "null", m.group(1)))
    except ValueError:
        return None


def apply_note(item: Item, note: dict) -> None:
    item.text = str(note.get("desc") or "").strip()
    if note.get("title"):
        item.title = str(note["title"]).strip()
    item.published = from_timestamp(note.get("time")) or item.published
    ii = note.get("interactInfo") or {}
    item.likes = parse_count(ii.get("likedCount")) or item.likes
    item.collects = parse_count(ii.get("collectedCount")) or item.collects
    item.comments = parse_count(ii.get("commentCount")) or item.comments
    item.shares = parse_count(ii.get("shareCount")) or item.shares
    item.tags = [str(t.get("name")) for t in note.get("tagList") or [] if isinstance(t, dict) and t.get("name")][:10]
    imgs = []
    for im in note.get("imageList") or []:
        if not isinstance(im, dict):
            continue
        u = im.get("urlDefault") or im.get("url_default") or im.get("url") or ""
        for info in im.get("infoList") or []:
            if isinstance(info, dict) and info.get("imageScene") in ("WB_DFT", "CRD_WM_WEBP") and info.get("url"):
                u = u or info["url"]
        if u:
            imgs.append(u if u.startswith("http") else "https:" + u)
    item.images = imgs or item.images
    if note.get("type") == "video":
        item.kind = "视频"
        stream = ((note.get("video") or {}).get("media") or {}).get("stream") or {}
        urls = []
        for codec in ("h264", "h265", "av1"):
            for s in stream.get(codec) or []:
                if isinstance(s, dict):
                    urls += [u for u in [s.get("masterUrl")] + list(s.get("backupUrls") or []) if u]
        item.media_urls = urls
        item.referer = HOME + "/"
        item.duration_ms = int(((note.get("video") or {}).get("capa") or {}).get("duration") or 0) * 1000


def detail(b, cfg, item: Item) -> None:
    b.goto(item.url)
    state = b.eval(_STATE_JS) or _state_from_html(b.page.content() if b.page else "")
    if not state:
        return
    nid = item.extra.get("note_id") or item.id.removeprefix("xhs-")
    found = walk(state, lambda d: (d.get("noteId") == nid or "interactInfo" in d) and "desc" in d)
    if found:
        apply_note(item, found[0])


def hot(b, cfg, limit: int, log=print) -> Result:
    """小红书没有公开热榜，这里取搜索框里的「热门搜索」"""
    base = _base(cfg)

    def open_search_box():
        for sel in ("#search-input", "input[placeholder*='搜索']"):
            try:
                b.page.click(sel, timeout=3000)
                b.page.wait_for_timeout(2000)
                return
            except Exception:  # noqa: BLE001
                continue

    raw = b.page_collect(base + "/explore", (API_HOT,),
                         lambda body: [x for x in ((body.get("data") or {}).get("items") or []) if isinstance(x, dict)],
                         limit, rounds=3, idle_rounds=2, max_seconds=30,
                         key=lambda x: str(x.get("title") or x.get("word") or ""), after_open=open_search_box)
    out = []
    for i, x in enumerate(raw[:limit], 1):
        word = str(x.get("title") or x.get("word") or "").strip()
        if word:
            out.append(HotItem(NAME, i, word, f"{base}/search_result?keyword={quote(word)}",
                               heat=str(x.get("score") or ""), label=str(x.get("word_type") or "")))
    if not out:
        return Result(reason="blocked", detail="没拿到热门搜索（可能需要登录）")
    return Result(hot=out)
