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
        "create_time": int(time.time()) - (i + 1) * 86400,
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


LOGIN_PAGE = """<html><body>
<div>douyin home</div><button id="login" onclick="openLogin()">登录</button>
<div id="dlg" style="display:none;padding:30px;background:#fff">
  <span onclick="tab('qrbox')">扫码登录</span> <span onclick="tab('smsbox')">验证码登录</span>
  <div id="qrbox"><p>扫码登录</p><p id="tip">打开抖音App扫码</p>
    <div class="qrcode-wrapper"><img id="qr" width="180" height="180"></div></div>
  <div id="smsbox" style="display:none">
    <input id="phone" placeholder="请输入手机号" type="tel">
    <input id="pcode" placeholder="请输入验证码" maxlength="6">
    <span onclick="sendSms()">获取验证码</span>
    <label><input type="checkbox" id="agree"> 我已阅读并同意用户协议</label>
    <button onclick="phoneLogin()">登录</button><p id="tip2"></p>
  </div>
</div>
<div id="slider" style="display:none">请完成下列验证后继续 拖动滑块</div>
<div id="verify" style="display:none">
  <p>身份验证</p><span onclick="smsOpt=true">接收短信验证码</span>
  <input id="code" placeholder="请输入验证码" maxlength="6">
  <span onclick="fetch('/test/send?p=bound')">获取验证码</span><button onclick="submitCode()">验证</button>
</div>
<script>
let gen = 0, smsOpt = false;
function tab(id) { for (const t of ['qrbox', 'smsbox']) document.getElementById(t).style.display = t === id ? 'block' : 'none'; }
async function sendSms() {
  const d = await (await fetch('/test/send?p=' + document.getElementById('phone').value
    + '&agree=' + document.getElementById('agree').checked)).json();
  if (d.captcha) { document.getElementById('slider').style.display = 'block';
    document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('slider').style.display = 'none'; }); } }
async function phoneLogin() {
  const d = await (await fetch('/test/code?c=' + document.getElementById('pcode').value)).json();
  if (d.ok) document.cookie = 'sessionid=abc; path=/; max-age=86400'; else document.getElementById('tip2').textContent = '验证码错误'; }
function qrSrc(g) { return 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180"><rect width="180" height="180" fill="#fff"/>'
  + '<rect x="10" y="10" width="' + (40 + g * 10) + '" height="60" fill="#000"/></svg>'); }
function openLogin() { document.getElementById('dlg').style.display = 'block';
  document.getElementById('qr').src = qrSrc(gen); }
async function submitCode() {
  const r = await fetch('/test/code?c=' + document.getElementById('code').value); const d = await r.json();
  if (d.ok) document.cookie = 'sessionid=abc; path=/; max-age=86400'; }
setInterval(async () => {
  const d = await (await fetch('/test/qrstatus')).json();
  const tip = document.getElementById('tip');
  if (d.s === 'scanned') tip.textContent = '扫码成功，请在手机上确认';
  if (d.s === 'expired') tip.innerHTML = '二维码已失效 <a onclick="refresh()">刷新</a>';
  if (d.s === 'confirmed') document.cookie = 'sessionid=abc; path=/; max-age=86400';
  if (d.s === 'verify') { document.getElementById('dlg').style.display = 'none';
    document.getElementById('verify').style.display = 'block'; }
}, 700);
async function refresh() { await fetch('/test/refresh'); gen += 1; document.getElementById('qr').src = qrSrc(gen);
  document.getElementById('tip').textContent = '打开抖音App扫码'; }
</script></body></html>"""


class FakeDouyin:
    def __init__(self, n_favorites: int = 45, mode: str = "ok", login_page: bool = False):
        self.login_page = login_page
        self.qr_state = "waiting"   # waiting / scanned / confirmed / expired / verify
        self.sms_code = "123456"
        self.sms_to: list = []        # 发过短信的 (手机号, 是否勾了协议)
        self.send_captcha = False     # 发短信时弹滑块
        self.mode = mode
        self.n_favorites = n_favorites
        self.requests: list[str] = []
        self.queries: list[str] = []
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
                if u.path == "/aweme/v1/web/hot/search/list/":
                    words = [{"word": f"抖音热点{i}", "hot_value": 11000000 - i * 100000, "label": 3 if i == 0 else 0,
                              "sentence_id": str(2000 + i)} for i in range(15)]
                    return self._send(200, json.dumps({"status_code": 0, "data": {"word_list": words}}, ensure_ascii=False))
                if u.path == "/aweme/v1/web/search/item/":
                    fake.queries.append(self.path)
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
                if u.path == "/test/qrstatus":
                    return self._send(200, json.dumps({"s": fake.qr_state}))
                if u.path == "/test/refresh":
                    fake.qr_state = "waiting"
                    return self._send(200, "{}")
                if u.path == "/test/send":
                    fake.sms_to.append((q.get("p", [""])[0], q.get("agree", [""])[0]))
                    return self._send(200, json.dumps({"captcha": fake.send_captcha}))
                if u.path == "/test/code":
                    ok = q.get("c", [""])[0] == fake.sms_code
                    return self._send(200, json.dumps({"ok": ok}))
                if fake.login_page and u.path == "/":
                    return self._send(200, LOGIN_PAGE, "text/html")
                if u.path.startswith("/video/"):
                    return self._send(200, "<html><body>video</body></html>", "text/html")
                self._send(200, "<html><body>douyin home</body></html>", "text/html")

        return H
