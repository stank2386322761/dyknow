"""微博：热搜榜（不需要登录）。"""

from __future__ import annotations

from urllib.parse import quote

from . import HotItem, Result, site_url

NAME = "weibo"
WWW = "https://weibo.com"
S = "https://s.weibo.com"
NEEDS_LOGIN = False
LOGIN_URL = WWW + "/login.php"


def logged_in(b, cfg) -> bool | None:
    return None


def search(b, cfg, keyword, limit, days, log=print) -> Result:
    return Result(reason="unsupported", detail="微博只抓热搜，不做关键词搜索")


def hot(b, cfg, limit: int, log=print) -> Result:
    www = site_url(cfg, NAME, "www", WWW)
    s = site_url(cfg, NAME, "s", S)
    rows = []
    try:
        b.goto(www + "/")
        data = b.fetch_json(www + "/ajax/side/hotSearch")
        rows = [r for r in ((data.get("data") or {}).get("realtime") or []) if isinstance(r, dict)]
    except Exception:  # noqa: BLE001  接口不行就读热搜页面
        rows = []
    out = []
    for r in rows:
        if r.get("is_ad"):
            continue
        word = str(r.get("word") or r.get("note") or "").strip()
        if word:
            out.append(HotItem(NAME, len(out) + 1, word, f"{s}/weibo?q={quote('#' + word + '#')}",
                               heat=str(r.get("num") or ""), label=str(r.get("label_name") or "")))
        if len(out) >= limit:
            break
    if not out:
        b.goto(s + "/top/summary")
        cells = b.eval("""() => Array.from(document.querySelectorAll('td.td-02')).map(td => {
            const a = td.querySelector('a'); const sp = td.querySelector('span');
            return {word: a ? a.innerText : '', num: sp ? sp.innerText : ''}; })""") or []
        out = [HotItem(NAME, i, c["word"].strip(), f"{s}/weibo?q={quote('#' + c['word'].strip() + '#')}", heat=c.get("num", ""))
               for i, c in enumerate([c for c in cells if c.get("word", "").strip()][:limit], 1)]
    if not out:
        return Result(reason="blocked", detail="没拿到热搜（可能需要登录）")
    return Result(hot=out)
