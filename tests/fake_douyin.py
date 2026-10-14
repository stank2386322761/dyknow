"""本地假抖音：模拟收藏、搜索、博主作品、详情、评论接口和视频文件，用来测试浏览器层。

mode:
  ok       页面内接口请求正常返回
  blocked  直接发的接口请求返回空（模拟需要签名），只有页面自己发的请求（带 from_page=1）有数据
  dead     全部返回空
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pathlib import Path

AUTHOR = {"nickname": "测试博主", "sec_uid": "MS4wLjABAAAAtest"}
FIXTURES = Path(__file__).parent / "fixtures"
WITH_AUDIO = (FIXTURES / "scenes_audio.mp4").read_bytes()   # 有声音、有 3-4 个场景
VIDEO_ONLY = (FIXTURES / "video_only.mp4").read_bytes()     # 只有画面（模拟 DASH 分轨）
SILENT_IDS: set[str] = set()                                 # 这些作品所有地址都只有画面


def make_aweme(i: int, base: str, image: bool = False) -> dict:
    aid = str(7400000000000000000 + i)
    aw = {
        "aweme_id": aid,
        "desc": f"第{i}条视频：AI 工具实测 #AI工具 #效率",
        "create_time": 1760000000 - i * 86400,
        "author": AUTHOR,
        "statistics": {"digg_count": 12000 + i, "comment_count": 30, "collect_count": 500, "share_count": 9},
        "text_extra": [{"hashtag_name": "AI工具"}, {"hashtag_name": "效率"}],
        "aweme_type": 68 if image else 0,
        "video": {
            "duration": 8000,
            "play_addr": {"url_list": [f"{base}/media/{aid}.mp4"]},
            # 和真实抖音一样：码率最低的是 DASH 视频分轨（没有声音）
            "bit_rate": [
                {"format": "dash", "bit_rate": 117073, "play_addr": {"url_list": [f"{base}/media/{aid}-dash.mp4"]}},
                {"format": "mp4", "bit_rate": 168341, "play_addr": {"url_list": [f"{base}/media/{aid}.mp4?low=1"]}},
            ],
        },
    }
    if image:
        aw["images"] = [{"url_list": [f"{base}/img/{aid}_{k}.webp", f"{base}/img/{aid}_{k}.jpeg"]} for k in range(3)]
    return aw


PAGE_JS = """
<script>
let cursor = 0, loading = false;
async function more() {
  if (loading) return; loading = true;
  const x = new XMLHttpRequest();
  x.open('POST', '/aweme/v1/web/aweme/listcollection/?from_page=1');
  x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
  x.onload = () => { try { const d = JSON.parse(x.responseText); cursor = d.cursor; } catch(e){}; loading = false; };
  x.send('count=20&cursor=' + cursor);
}
more();
window.addEventListener('scroll', () => more());
</script>
<div style="height:20000px">favorites</div>
"""


class FakeDouyin:
    def __init__(self, n_favorites: int = 45, mode: str = "ok"):
        self.mode = mode
        self.n_favorites = n_favorites
        self.requests: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.favorites = [make_aweme(i, self.base, image=(i == 3)) for i in range(n_favorites)]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

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
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _logged_in(self):
                if fake.mode == "expired":
                    return False
                return "sessionid=" in (self.headers.get("Cookie") or "")

            def _api_allowed(self, q):
                if fake.mode in ("dead", "captcha"):
                    return False
                if fake.mode == "blocked":
                    return q.get("from_page") == ["1"]
                return True

            def do_POST(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                length = int(self.headers.get("Content-Length") or 0)
                form = parse_qs(self.rfile.read(length).decode())
                fake.requests.append(u.path)
                if u.path.endswith("/listcollection/"):
                    if not self._api_allowed(q):
                        return self._send(200, "")
                    if not self._logged_in():
                        return self._send(200, json.dumps({"status_code": 8, "status_msg": "用户未登录"}))
                    cur = int(form.get("cursor", ["0"])[0])
                    cnt = int(form.get("count", ["20"])[0])
                    items = fake.favorites[cur:cur + cnt]
                    nxt = cur + len(items)
                    return self._send(200, json.dumps({"status_code": 0, "aweme_list": items, "cursor": nxt,
                                                       "has_more": int(nxt < len(fake.favorites))}))
                self._send(404, "")

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                fake.requests.append(u.path)
                if u.path.startswith("/media/"):
                    vid = u.path.split("/")[-1].split(".")[0].replace("-dash", "")
                    data = VIDEO_ONLY if ("-dash" in u.path or vid in SILENT_IDS) else WITH_AUDIO
                    return self._send(200, data, "video/mp4")
                if u.path.startswith("/img/"):
                    ctype = "image/jpeg" if u.path.endswith(".jpeg") else "image/webp"
                    return self._send(200, b"\xff\xd8" + b"\x00" * 20_000, ctype)
                if fake.mode == "captcha" and not u.path.startswith("/aweme/"):
                    return self._send(200, "<html><body>请完成下列验证后继续 拖动滑块</body></html>", "text/html")
                if u.path == "/user/self":
                    return self._send(200, "<html><body>" + PAGE_JS + "</body></html>", "text/html")
                if u.path.startswith("/aweme/") and "stream" not in u.path:
                    if not self._api_allowed(q):
                        return self._send(200, "")
                if u.path == "/aweme/v1/web/search/item/":
                    off = int(q.get("offset", ["0"])[0])
                    items = [{"type": 1, "aweme_info": a} for a in fake.favorites[off:off + 10]]
                    return self._send(200, json.dumps({"status_code": 0, "data": items, "cursor": off + 10,
                                                       "has_more": 1}))
                if u.path == "/aweme/v1/web/aweme/post/":
                    cur = int(q.get("max_cursor", ["0"])[0] or 0)
                    items = fake.favorites[cur:cur + 18]
                    return self._send(200, json.dumps({"status_code": 0, "aweme_list": items, "max_cursor": cur + 18,
                                                       "has_more": int(cur + 18 < len(fake.favorites))}))
                if u.path == "/aweme/v1/web/aweme/detail/":
                    aid = q.get("aweme_id", [""])[0]
                    aw = next((a for a in fake.favorites if a["aweme_id"] == aid), None)
                    return self._send(200, json.dumps({"status_code": 0, "aweme_detail": aw}))
                if u.path == "/aweme/v1/web/comment/list/":
                    comments = [{"cid": str(i), "text": f"评论{i}", "digg_count": i * 10} for i in range(8)]
                    return self._send(200, json.dumps({"status_code": 0, "comments": comments, "has_more": 0}))
                if u.path.startswith("/search/"):
                    js = """<script>
fetch('/aweme/v1/web/search/stream/?keyword=x');  // 永远不结束的流式响应（真实抖音有类似接口）
let off = 0;
function more(){ const x = new XMLHttpRequest();
  x.open('GET', '/aweme/v1/web/search/item/?from_page=1&offset=' + off); x.send(); off += 10; }
more(); window.addEventListener('scroll', more);
</script><div style="height:20000px">search</div>"""
                    return self._send(200, "<html><body>" + js + "</body></html>", "text/html")
                if u.path == "/aweme/v1/web/search/stream/":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"data": [')
                    self.wfile.flush()
                    time.sleep(120)
                    return
                if u.path.startswith("/video/"):
                    return self._send(200, "<html><body>video</body></html>", "text/html")
                self._send(200, "<html><body>douyin home</body></html>", "text/html")

        return H
