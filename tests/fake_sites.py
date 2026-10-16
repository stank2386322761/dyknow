"""本地假网站：小红书、知乎、B站、微博（按目前对这些网站网页版数据结构的了解模拟）。

真实结构如果不同，用 dyknow 保存的原始数据（cache/raw）对照修改这里和 dyknow/sites/*.py。
所有平台共用一个端口；登录状态看 Cookie：web_session=loggedin（小红书）、z_c0（知乎）。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from fake_douyin import WITH_AUDIO

NOW = int(time.time())
DAY = 86400


def xhs_items(page: int, broken: bool = False) -> list[dict]:
    out = []
    for k in range(10):
        i = page * 10 + k
        if broken:
            out.append({"id": f"{i:024x}", "model_type": "note", "card": {"title": "结构变了"}})
            continue
        old = i % 7 == 6
        out.append({
            "id": f"{0x64f000 + i:024x}", "model_type": "note", "xsec_token": f"tok{i}",
            "note_card": {
                "display_title": f"小红书笔记{i}：AI 编程入门 | 第{i}篇", "type": "video" if i == 2 else "normal",
                "user": {"nickname": f"作者{i}"},
                "interact_info": {"liked_count": f"{(i + 1) * 0.3:.1f}万" if i % 2 else str(100 + i),
                                  "collected_count": "520", "comment_count": "33", "shared_count": "8"},
                "corner_tag_info": [{"type": "publish_time", "text": "2023-01-05" if old else f"{i % 5 + 1}天前"}],
                "cover": {"url_default": "/img/cover.jpg"},
            },
        })
    return out


def xhs_note(nid: str, base: str) -> dict:
    is_video = nid.endswith("7")  # 点赞最高的那条（第 39 条）是视频笔记
    note = {
        "noteId": nid, "title": "AI 编程入门完整版", "desc": "第一步装好工具\n第二步写第一个项目 #AI编程[话题]#",
        "time": (NOW - 2 * DAY) * 1000, "type": "video" if is_video else "normal",
        "interactInfo": {"likedCount": "1.5万", "collectedCount": "8000", "commentCount": "300", "shareCount": "50"},
        "tagList": [{"name": "AI编程"}, {"name": "Cursor"}],
        "imageList": [{"urlDefault": f"{base}/img/{nid}_{k}.jpg"} for k in range(3)],
    }
    if is_video:
        note["video"] = {"media": {"stream": {"h264": [{"masterUrl": f"{base}/media/{nid}.mp4"}]}}, "capa": {"duration": 8}}
    return note


def zhihu_results(offset: int) -> list[dict]:
    out = []
    for k in range(10):
        i = offset + k
        typ = "article" if i % 4 == 3 else "answer"
        created = NOW - (400 * DAY if i % 6 == 5 else (i + 1) * DAY)
        obj = {"type": typ, "id": str(1000 + i), "voteup_count": 50 * (i % 9 + 1), "comment_count": 12,
               "created_time": created, "author": {"name": f"答主{i}"},
               "excerpt": f"这是第 {i} 条的<em>摘要</em>"}
        if typ == "answer":
            obj["question"] = {"id": str(500 + i), "name": f"<em>AI编程</em>该怎么入门？（{i}）"}
        else:
            obj["title"] = f"<em>AI编程</em>学习路线（{i}）"
        out.append({"type": "search_result", "object": obj})
    return out


def bili_results(page: int) -> list[dict]:
    out = []
    for k in range(10):
        i = page * 10 + k
        out.append({"type": "video", "bvid": f"BV1xx{i:05d}", "title": f'<em class="keyword">AI编程</em>教程 {i}',
                    "author": f"UP主{i}", "play": 10000 * (i + 1), "like": 300 * (i % 5 + 1), "favorites": 100,
                    "review": 20, "pubdate": NOW - (i % 10 + 1) * DAY, "description": f"简介{i}", "duration": "5:21"})
    return out


class FakeSites:
    def __init__(self, xhs_broken: bool = False):
        self.xhs_broken = xhs_broken
        self.requests: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def urls(self) -> dict:
        return {k: self.base for k in ("xhs.www", "zhihu.www", "zhihu.zhuanlan", "bili.www", "bili.search",
                                       "weibo.www", "weibo.s")}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/json")) else ""))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _html(self, body):
                return self._send(200, f"<html><body>{body}</body></html>", "text/html")

            def _cookie(self):
                return self.headers.get("Cookie") or ""

            def do_POST(self):
                u = urlparse(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                fake.requests.append(self.path)
                if u.path == "/api/sns/web/v1/search/notes":
                    if "web_session=loggedin" not in self._cookie():
                        return self._send(200, {"code": -100, "success": False, "msg": "登录已过期"})
                    page = int(body.get("page", 1)) - 1
                    return self._send(200, {"code": 0, "success": True,
                                            "data": {"has_more": page < 2, "items": xhs_items(page, fake.xhs_broken)}})
                self._send(404, "")

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                fake.requests.append(self.path)
                p = u.path
                # ── 小红书 ──
                if p == "/explore":
                    return self._html("""<input id="search-input" placeholder="搜索小红书">
<script>
const x = new XMLHttpRequest(); x.open('GET', '/api/sns/web/v2/user/me'); x.send();
document.getElementById('search-input').addEventListener('click', () => {
  const h = new XMLHttpRequest(); h.open('GET', '/api/sns/web/v1/search/hot_list'); h.send(); });
</script>""")
                if p == "/api/sns/web/v2/user/me":
                    guest = "web_session=loggedin" not in self._cookie()
                    return self._send(200, {"code": 0, "success": True, "data": {"guest": guest}})
                if p == "/api/sns/web/v1/search/hot_list":
                    return self._send(200, {"code": 0, "data": {"items": [{"title": f"热搜{i}", "score": str(1000 - i)} for i in range(8)]}})
                if p == "/search_result":
                    return self._html("""<div onclick="this.dataset.open=1">筛选</div><span>最多点赞</span><span>一周内</span>
<script>
let page = 1;
function more(){ const x = new XMLHttpRequest(); x.open('POST', '/api/sns/web/v1/search/notes');
  x.setRequestHeader('Content-Type','application/json'); x.send(JSON.stringify({keyword: 'x', page: page++})); }
more(); window.addEventListener('scroll', more);
</script><div style="height:20000px"></div>""")
                if p.startswith("/explore/"):
                    nid = p.split("/")[-1]
                    state = json.dumps({"note": {"noteDetailMap": {nid: {"note": xhs_note(nid, fake.base)}}}}, ensure_ascii=False)
                    return self._html(f"<script>window.__INITIAL_STATE__={state}</script>")
                # ── 知乎 ──
                if p == "/search":
                    return self._html("""<script>
let off = 0;
function more(){ const x = new XMLHttpRequest(); x.open('GET', '/api/v4/search_v3?t=general&offset=' + off); x.send(); off += 10; }
more(); window.addEventListener('scroll', more);
</script><div style="height:20000px"></div>""")
                if p == "/api/v4/search_v3":
                    if "z_c0=" not in self._cookie():
                        return self._send(401, {"error": {"message": "请登录"}})
                    off = int(q.get("offset", ["0"])[0])
                    return self._send(200, {"data": zhihu_results(off), "paging": {"is_end": off >= 20}})
                if p.startswith("/question/") or p.startswith("/p/"):
                    oid = p.split("/")[-1]
                    ents = {"answers": {oid: {"content": "<p>第一段：先选工具</p><p>第二段：<b>做项目</b></p>", "voteupCount": 999}},
                            "articles": {oid: {"content": "<h2>路线</h2><p>文章正文</p>", "voteupCount": 888}}}
                    data = json.dumps({"initialState": {"entities": ents}}, ensure_ascii=False)
                    return self._html(f'<script id="js-initialData" type="text/json">{data}</script>')
                if p == "/billboard":
                    rows = [{"target": {"titleArea": {"text": f"知乎热榜问题{i}"}, "metricsArea": {"text": f"{900 - i} 万热度"},
                                        "link": {"url": f"https://www.zhihu.com/question/{9000 + i}"}}} for i in range(12)]
                    data = json.dumps({"initialState": {"topstory": {"hotList": rows}}}, ensure_ascii=False)
                    return self._html(f'<script id="js-initialData" type="text/json">{data}</script>')
                # ── B站 ──
                if p == "/video" and "keyword" in q:
                    return self._html("""<script>
let pg = 1;
function more(){ const x = new XMLHttpRequest(); x.open('GET', '/x/web-interface/wbi/search/type?search_type=video&page=' + pg++); x.send(); }
more(); window.addEventListener('scroll', more);
</script><div style="height:20000px"></div>""")
                if p == "/x/web-interface/wbi/search/type":
                    pg = int(q.get("page", ["1"])[0]) - 1
                    return self._send(200, {"code": 0, "data": {"result": bili_results(pg) if pg < 3 else []}})
                if p.startswith("/video/BV"):
                    bv = p.split("/")[-1]
                    st = json.dumps({"videoData": {"desc": f"{bv} 的简介", "stat": {"like": 4321, "favorite": 99, "reply": 7, "view": 88888},
                                                   "pubdate": NOW - DAY, "duration": 8}})
                    play = json.dumps({"data": {"dash": {"audio": [{"bandwidth": 90000, "baseUrl": f"{fake.base}/media/{bv}-hi.m4a"},
                                                                   {"bandwidth": 30000, "baseUrl": f"{fake.base}/media/{bv}.m4a"}]}}})
                    return self._html(f"<script>window.__INITIAL_STATE__={st};window.__playinfo__={play}</script>")
                if p == "/v/popular/all":
                    return self._html("""<script>const x = new XMLHttpRequest(); x.open('GET', '/x/web-interface/popular?ps=20'); x.send();</script>""")
                if p == "/x/web-interface/popular":
                    lst = [{"bvid": f"BVhot{i}", "title": f"B站热门{i}", "owner": {"name": f"UP{i}"}, "stat": {"view": 1230000 - i}} for i in range(10)]
                    return self._send(200, {"code": 0, "data": {"list": lst}})
                # ── 微博 ──
                if p == "/ajax/side/hotSearch":
                    rt = [{"word": f"微博热搜{i}", "num": 500000 - i, "label_name": "热" if i == 0 else ""} for i in range(10)]
                    rt.insert(2, {"word": "广告位", "is_ad": 1})
                    return self._send(200, {"ok": 1, "data": {"realtime": rt}})
                # ── 媒体 ──
                if p.startswith("/media/"):
                    return self._send(200, WITH_AUDIO, "video/mp4")
                if p.startswith("/img/"):
                    return self._send(200, b"\xff\xd8" + b"\x00" * 20_000, "image/jpeg")
                return self._html("home")

        return H
