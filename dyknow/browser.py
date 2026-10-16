"""浏览器层：用一个固定的浏览器目录保持登录，在已登录的抖音页面里取数据。

两种取数方式，自动切换：
  1. 接口方式：在抖音页面里直接发请求（带着页面自己的登录态），速度快、能翻页
  2. 页面方式：接口取不到时，打开对应页面、边滚动边记录页面自己加载的数据

所有失败都归到明确的原因（未登录 / 被限流 / 浏览器被占用 / 没有数据），
由调用方原样告诉用户，不让 AI 去猜。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote, urlparse

from .config import Config

API_COLLECTION = "/aweme/v1/web/aweme/listcollection/"
API_SEARCH = "/aweme/v1/web/search/item/"
API_POST = "/aweme/v1/web/aweme/post/"
API_DETAIL = "/aweme/v1/web/aweme/detail/"
API_COMMENTS = "/aweme/v1/web/comment/list/"

# 失败原因（调用方据此给出下一步提示）
LOGIN = "login"          # 未登录 / 登录失效
BLOCKED = "blocked"      # 接口和页面都拿不到数据：限流、验证码或页面改版
EMPTY = "empty"          # 确实没有内容（例如收藏为空、搜索无结果）
BUSY = "busy"            # 浏览器目录被另一个进程占用
NO_BROWSER = "no_browser"

REASON_TEXT = {
    LOGIN: "抖音未登录或登录已失效",
    BLOCKED: "抖音没有返回数据（可能被限流、需要验证码，或页面改版）",
    EMPTY: "没有内容",
    BUSY: "登录用的浏览器目录正被占用（另一个 dyknow 正在运行，或上次的浏览器窗口没关）",
    NO_BROWSER: "找不到可用的浏览器（需要 Chrome、Edge，或运行 playwright install chromium）",
}

_SCROLL_JS = """() => {
  for (const el of document.querySelectorAll('div, section, main, ul')) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 40) {
      el.scrollTop = el.scrollHeight;
    }
  }
  window.scrollTo(0, document.body.scrollHeight);
}"""

_UNSCROLL_JS = """() => {
  for (const el of document.querySelectorAll('div, section, main, ul')) {
    if (el.scrollTop > 0) el.scrollTop = Math.max(0, el.scrollTop - 600);
  }
  window.scrollBy(0, -600);
}"""

# 在页面里用 XMLHttpRequest 发请求：抖音页面的安全脚本会给页面内的请求自动补签名参数
_XHR_JS = """async ({path, params, method, body}) => {
  const ua = navigator.userAgent;
  const ver = (ua.match(/Chrome\\/([\\d.]+)/) || [,'130.0.0.0'])[1];
  const common = {
    device_platform: 'webapp', aid: '6383', channel: 'channel_pc_web', pc_client_type: '1',
    update_version_code: '170400', version_code: '190500', version_name: '19.5.0',
    cookie_enabled: String(navigator.cookieEnabled), screen_width: String(screen.width),
    screen_height: String(screen.height), browser_language: navigator.language || 'zh-CN',
    browser_platform: navigator.platform || 'Win32', browser_name: 'Chrome', browser_version: ver,
    browser_online: 'true', engine_name: 'Blink', engine_version: ver, platform: 'PC',
    cpu_core_num: String(navigator.hardwareConcurrency || 8), device_memory: String(navigator.deviceMemory || 8),
    downlink: '10', effective_type: '4g', round_trip_time: '50',
  };
  try { const t = localStorage.getItem('xmst'); if (t) common.msToken = t; } catch (e) {}
  const qs = new URLSearchParams({...common, ...params});
  return await new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, path + '?' + qs.toString(), true);
    xhr.withCredentials = true;
    xhr.timeout = 20000;
    if (method === 'POST') xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.onload = () => resolve({http: xhr.status, text: xhr.responseText || ''});
    xhr.onerror = () => resolve({http: 0, text: ''});
    xhr.ontimeout = () => resolve({http: -1, text: ''});
    xhr.send(body ? new URLSearchParams(body).toString() : null);
  });
}"""


class BrowserError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(REASON_TEXT.get(reason, reason) + (f"：{detail}" if detail else ""))


class ApiFail(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


@dataclass
class Fetch:
    """一次抓取的结果。items 是接口原始作品对象（aweme dict）"""
    items: list[dict] = field(default_factory=list)
    reason: str = ""      # 空 = 成功
    via: str = ""         # api / page
    detail: str = ""
    stopped_at_known: bool = False
    examined: int = 0     # 实际检查了多少条（含已有的）


def classify(resp: dict) -> dict:
    """把页面内请求的返回解析成 JSON；失败时抛出带原因的 ApiFail"""
    http = int(resp.get("http") or 0)
    text = str(resp.get("text") or "")
    if http in (401,):
        raise ApiFail(LOGIN, f"HTTP {http}")
    if not text.strip():
        raise ApiFail(BLOCKED, f"HTTP {http}，返回为空")
    try:
        data = json.loads(text)
    except ValueError:
        raise ApiFail(BLOCKED, f"HTTP {http}，返回不是数据（{text[:40]!r}）")
    if not isinstance(data, dict):
        raise ApiFail(BLOCKED, "返回格式不对")
    code = data.get("status_code", 0)
    if code not in (0, None):
        msg = str(data.get("status_msg") or "")
        if code in (8, 2483) or "登录" in msg or "login" in msg.lower():
            raise ApiFail(LOGIN, msg or f"status_code={code}")
        raise ApiFail(BLOCKED, msg or f"status_code={code}")
    return data


class ProfileLock:
    """进程间互斥：同一个浏览器目录同时只能有一个 dyknow 在用。
    不依赖 Chrome 自己的检测（无界面模式下它不拦），用系统文件锁，进程退出自动释放。"""

    def __init__(self, path):
        self.path = path
        self._f = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise BrowserError(BUSY)
        self._f = f

    def release(self) -> None:
        if self._f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._f.close()
        self._f = None


class Douyin:
    def __init__(self, cfg: Config, headless: bool | None = None, log: Callable[[str], None] = print,
                 start_url: str | None = None):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/")
        # 打开浏览器后先去的页面；其他平台传 ""（不预先打开抖音）
        self.start_url = self.base + "/" if start_url is None else start_url
        self.raw_dir = None  # 设置后，页面方式抓到的原始数据会存一份（排查解析问题用）
        self._raw_n = 0
        self.captured = 0    # 页面方式最近一次收到了多少个接口返回（有返回但解析不出 = 网站结构变了）
        self.headless = cfg.get("headless") if headless is None else headless
        self.log = log
        self._pw = None
        self.ctx = None
        self.page = None
        self._watch: str | tuple = ""
        self._responses: list = []
        self.channel_used = ""
        self._lock: ProfileLock | None = None

    # ── 生命周期 ──────────────────────────────

    def __enter__(self) -> "Douyin":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        from playwright.sync_api import sync_playwright

        self.cfg.profile_dir.mkdir(parents=True, exist_ok=True)
        self._lock = ProfileLock(self.cfg.home / "browser.lock")
        self._lock.acquire()
        self._pw = sync_playwright().start()
        pref = str(self.cfg.get("browser") or "auto")
        channels = ["chrome", "msedge", None] if pref == "auto" else [None if pref == "chromium" else pref]
        last_err = ""
        for ch in channels:
            kwargs = dict(
                user_data_dir=str(self.cfg.profile_dir),
                headless=bool(self.headless),
                viewport={"width": 1280, "height": 860},
                locale="zh-CN",
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            if ch:
                kwargs["channel"] = ch
            try:
                self.ctx = self._pw.chromium.launch_persistent_context(**kwargs)
                self.channel_used = ch or "chromium"
                break
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                low = last_err.lower()
                if any(k in low for k in ("singleton", "already in use", "existing browser session")):
                    self.close()
                    raise BrowserError(BUSY)
        if self.ctx is None:
            self.close()
            raise BrowserError(NO_BROWSER, last_err.splitlines()[0][:120] if last_err else "")
        try:
            self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
            self.page.set_default_timeout(30_000)
            # 只收"已经下载完"的请求：直接读 response 会卡在一直不结束的流式接口上
            self.page.on("requestfinished", self._on_request_finished)
            if self.start_url:
                self._goto(self.start_url)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        try:
            if self.ctx is not None:
                self.ctx.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self.ctx = self._pw = self.page = None
        if getattr(self, "_lock", None) is not None:
            self._lock.release()
            self._lock = None

    def _goto(self, url: str) -> None:
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:  # noqa: BLE001
            raise BrowserError(BLOCKED, f"打开页面失败：{str(e).splitlines()[0][:100]}")
        self.page.wait_for_timeout(2500)

    def _on_request_finished(self, request) -> None:
        if not self._watch:
            return
        url = request.url
        watches = (self._watch,) if isinstance(self._watch, str) else self._watch
        if "stream" not in url and any(w in url for w in watches):
            self._responses.append(request)

    def _pause(self) -> None:
        base = float(self.cfg.get("request_interval") or 0)
        if base > 0:
            time.sleep(base + random.uniform(0, base / 2))

    # ── 登录 ──────────────────────────────────

    def logged_in(self) -> bool:
        try:
            cookies = self.ctx.cookies(self.base)
        except Exception:  # noqa: BLE001
            return False
        names = {c.get("name"): c.get("value") for c in cookies}
        if names.get("LOGIN_STATUS") == "1":
            return True
        if names.get("sessionid") or names.get("sessionid_ss"):
            return True
        try:
            return self.page.evaluate("() => localStorage.getItem('HasUserLogin')") == "1"
        except Exception:  # noqa: BLE001
            return False

    def wait_for_login(self, timeout: int = 300) -> bool:
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if self.logged_in():
                    self.page.wait_for_timeout(3000)  # 等登录相关 Cookie 写完
                    return True
                self.page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001  用户把浏览器窗口关了
            return False
        return False

    # ── 两种取数方式 ──────────────────────────

    def api(self, path: str, params: dict | None = None, method: str = "GET", body: dict | None = None) -> dict:
        if urlparse(self.page.url).netloc != urlparse(self.base).netloc:
            self._goto(self.base + "/")  # 页面内请求必须在抖音主站页面上发
        try:
            resp = self.page.evaluate(_XHR_JS, {"path": path, "params": {k: str(v) for k, v in (params or {}).items()},
                                                "method": method, "body": body})
        except Exception as e:  # noqa: BLE001
            raise ApiFail(BLOCKED, f"页面请求出错：{str(e).splitlines()[0][:80]}")
        return classify(resp if isinstance(resp, dict) else {})

    def page_collect(self, url: str, watch, extract: Callable[[dict], list[dict]],
                     limit: int, rounds: int = 40, idle_rounds: int = 6, max_seconds: int = 120,
                     key: Callable[[dict], str] | None = None,
                     after_open: Callable[[], None] | None = None) -> list[dict]:
        """打开页面、滚动，收集页面自己加载的接口数据（最多 max_seconds 秒，不会无限等）。
        watch：接口地址里包含的片段（可以是多个）；key：去重用的 ID；after_open：打开页面后先做的操作（如点筛选）"""
        key = key or (lambda a: str(a.get("aweme_id") or a.get("cid") or ""))
        self._show_window()
        self._responses = []
        self._watch = watch
        seen: set[str] = set()
        items: list[dict] = []
        idle = 0
        self.captured = 0
        deadline = time.time() + max_seconds
        try:
            self.log(f"   打开页面：{url.replace(self.base, '')[:70]}")
            self._goto(url)
            if after_open:
                try:
                    after_open()
                except Exception as e:  # noqa: BLE001  点筛选失败不影响抓取
                    self.log(f"   页面操作没成功（{str(e)[:50]}），按默认结果继续")
            for n in range(rounds):
                if time.time() > deadline:
                    self.log(f"   页面方式已用满 {max_seconds} 秒，停止（拿到 {len(items)} 条）")
                    break
                if n and n % 5 == 0:
                    self.log(f"   滚动第 {n} 轮，已拿到 {len(items)} 条")
                before = len(items)
                for r in self._drain():
                    self.captured += 1
                    for aw in extract(r):
                        aid = key(aw)
                        if aid and aid not in seen:
                            seen.add(aid)
                            items.append(aw)
                if len(items) >= limit:
                    break
                idle = idle + 1 if len(items) == before else 0
                if idle >= idle_rounds:
                    break
                self._scroll()
                self.page.wait_for_timeout(1800)
        finally:
            self._watch = ""
        return items[:limit]

    def _scroll(self) -> None:
        """先往回滚一点再滚到底，并模拟鼠标滚轮：已经在底部时也能触发「加载更多」"""
        try:
            self.page.evaluate(_UNSCROLL_JS)
            self.page.wait_for_timeout(300)
            self.page.mouse.move(640, 500)
            self.page.mouse.wheel(0, 4000)
            self.page.evaluate(_SCROLL_JS)
        except Exception:  # noqa: BLE001  页面跳转中
            pass

    def _drain(self) -> list[dict]:
        out = []
        pending, self._responses = self._responses, []
        for req in pending:
            try:
                resp = req.response()
                data = resp.json() if resp is not None else None
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict):
                out.append(data)
                self._dump(req.url, data)
        return out

    def _dump(self, url: str, data) -> None:
        if self.raw_dir is None:
            return
        try:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            self._raw_n += 1
            name = re.sub(r"[^A-Za-z0-9]+", "_", urlparse(url).path).strip("_")[-60:]
            (self.raw_dir / f"{self._raw_n:03d}_{name}.json").write_text(
                json.dumps({"url": url, "data": data}, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ── 通用小工具（其他平台用）──────────────────

    def goto(self, url: str) -> None:
        self._goto(url)

    def cookie_names(self, url: str) -> dict:
        try:
            return {c.get("name"): c.get("value") for c in self.ctx.cookies(url)}
        except Exception:  # noqa: BLE001
            return {}

    def eval(self, js: str, arg=None):
        """在页面里执行一段 JS，出错返回 None"""
        try:
            return self.page.evaluate(js, arg) if arg is not None else self.page.evaluate(js)
        except Exception:  # noqa: BLE001
            return None

    def click_text(self, texts: list[str], timeout: int = 2500) -> str:
        """依次尝试点击页面上写着这些字的元素，返回点中的那个（都点不中返回空串）"""
        for t in texts:
            try:
                el = self.page.get_by_text(t, exact=True).first
                if el.is_visible(timeout=timeout):
                    el.click(timeout=timeout)
                    self.page.wait_for_timeout(1500)
                    return t
            except Exception:  # noqa: BLE001
                continue
        return ""

    def fetch_json(self, url: str) -> dict:
        """在当前页面里用 fetch 取 JSON（同源请求，带登录态）"""
        resp = self.eval("""async (u) => { try { const r = await fetch(u, {credentials: 'include'});
            return {http: r.status, text: await r.text()}; } catch (e) { return {http: 0, text: ''}; } }""", url)
        data = classify(resp if isinstance(resp, dict) else {})
        self._dump(url, data)
        return data

    def _show_window(self) -> None:
        """页面方式需要窗口正常显示，最小化的窗口不会加载新内容"""
        if self.headless:
            return
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            win = cdp.send("Browser.getWindowForTarget")
            cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
        except Exception:  # noqa: BLE001
            pass

    def minimize(self) -> None:
        if self.headless:
            return
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            win = cdp.send("Browser.getWindowForTarget")
            cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "minimized"}})
        except Exception:  # noqa: BLE001
            pass

    # ── 通用翻页 ──────────────────────────────

    def _paged(self, fetch_page: Callable[[object], tuple[list[dict], object, bool]], limit: int,
               known: set[str] | None = None) -> Fetch:
        """接口方式翻页，最多检查 limit 条（已有的也算在内）。
        known：已有作品的 ID；遇到一整页都是已有的就提前停（收藏按时间倒序，后面都是旧的）"""
        result = Fetch(via="api")
        seen: set[str] = set()
        cursor: object = 0
        seen_cursors: set[str] = set()
        pages = 0
        while len(seen) < limit:
            try:
                page_items, next_cursor, has_more = fetch_page(cursor)
            except ApiFail as e:
                if not result.items and not seen:
                    raise
                result.detail = f"翻页中断：{e.detail}"
                break
            pages += 1
            new_on_page = 0
            for aw in page_items:
                aid = str(aw.get("aweme_id") or "")
                if not aid or aid in seen:
                    continue
                seen.add(aid)
                result.examined = len(seen)
                if known is not None and aid in known:
                    result.stopped_at_known = True
                    if len(seen) >= limit:
                        break
                    continue
                new_on_page += 1
                result.items.append(aw)
                if len(seen) >= limit:
                    break
            if pages % 5 == 0:
                self.log(f"   已检查 {len(seen)} 条")
            if known is not None and page_items and new_on_page == 0:
                break
            if not has_more or not page_items or str(next_cursor) in seen_cursors or next_cursor == cursor:
                break
            seen_cursors.add(str(cursor))
            cursor = next_cursor
            self._pause()
        return result

    def _with_fallback(self, name: str, api_call: Callable[[], Fetch], page_call: Callable[[], list[dict]],
                       need_login: bool) -> Fetch:
        if need_login and not self.logged_in():
            return Fetch(reason=LOGIN)
        try:
            res = api_call()
            if res.items or res.examined:
                return res
            api_detail = "接口返回 0 条"
        except ApiFail as e:
            if e.reason == LOGIN:
                return Fetch(reason=LOGIN, detail=e.detail)
            api_detail = e.detail
        self.log(f"   {name}：接口方式没拿到数据（{api_detail}），改用页面方式…")
        items = page_call()
        if items:
            return Fetch(items=items, via="page")
        state = self.page_state()
        if state == "captcha":
            return Fetch(reason=BLOCKED, detail="抖音要求完成验证码：运行 dyknow login，在弹出的浏览器里手动完成验证")
        if (need_login and not self.logged_in()) or state == "logged_out":
            return Fetch(reason=LOGIN)
        return Fetch(reason=BLOCKED, detail=f"接口：{api_detail}；页面方式也是 0 条")

    def page_state(self) -> str:
        """看当前页面上有没有验证码或登录框（页面方式拿不到数据时用来判断原因）"""
        try:
            text = self.page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 5000)")
        except Exception:  # noqa: BLE001
            return ""
        if any(k in text for k in ("请完成下列验证", "拖动滑块", "安全验证", "验证码中间页")):
            return "captcha"
        if "扫码登录" in text and any(k in text for k in ("验证码登录", "密码登录", "打开抖音App")):
            return "logged_out"
        return ""

    def open_login_dialog(self) -> None:
        """尽量帮用户点开登录框（点不到也没关系，用户可以自己点）"""
        try:
            btn = self.page.get_by_text("登录", exact=True).first
            if btn.is_visible(timeout=3000):
                btn.click(timeout=3000)
        except Exception:  # noqa: BLE001
            pass

    # ── 各数据来源 ────────────────────────────

    def favorites(self, limit: int, known: set[str] | None = None) -> Fetch:
        def page(cursor):
            data = self.api(API_COLLECTION, method="POST", body={"count": "20", "cursor": str(cursor)})
            return data.get("aweme_list") or [], data.get("cursor", 0), bool(data.get("has_more"))

        def from_page(body):
            return [a for a in body.get("aweme_list") or [] if isinstance(a, dict)]

        def page_way():
            return self.page_collect(self.base + "/user/self?showTab=favorite_collection",
                                     API_COLLECTION, from_page, limit)

        res = self._with_fallback("收藏", lambda: self._paged(page, limit, known), page_way, need_login=True)
        if res.via == "page":
            res.examined = len(res.items)
            if known:
                new = [a for a in res.items if str(a.get("aweme_id")) not in known]
                res.stopped_at_known = len(new) < len(res.items)
                res.items = new
        return res

    def search(self, keyword: str, limit: int, by_likes: bool = False, days: int = 0) -> Fetch:
        """by_likes：按最多点赞排序；days：只要最近多少天（抖音只支持 1 / 7 / 180 天这几档）"""
        from .douyin import awemes_from_search

        sort_type = "1" if by_likes else "0"
        publish_time = "0" if days <= 0 else "1" if days <= 1 else "7" if days <= 7 else "180"
        filtered = sort_type != "0" or publish_time != "0"

        def page(offset):
            data = self.api(API_SEARCH, {
                "keyword": keyword, "search_channel": "aweme_video_web",
                "search_source": "tab_search" if filtered else "normal_search",
                "query_correct_type": "1", "is_filter_search": "1" if filtered else "0",
                "offset": str(offset), "count": "10", "sort_type": sort_type, "publish_time": publish_time,
                "need_filter_settings": "0", "list_type": "single",
            })
            items = awemes_from_search(data)
            return items, int(data.get("cursor") or (int(offset) + 10)), bool(data.get("has_more"))

        url = f"{self.base}/search/{quote(keyword)}?type=video"
        if filtered:
            url += f"&sort_type={sort_type}&publish_time={publish_time}"
        return self._with_fallback(
            "搜索", lambda: self._paged(page, limit),
            lambda: self.page_collect(url, "/aweme/v1/web/search/", awemes_from_search, limit),
            need_login=False)

    def creator(self, sec_uid: str, limit: int) -> Fetch:
        def page(cursor):
            data = self.api(API_POST, {"sec_user_id": sec_uid, "max_cursor": str(cursor), "count": "18",
                                       "locate_query": "false", "publish_video_strategy_type": "2"})
            return data.get("aweme_list") or [], data.get("max_cursor", 0), bool(data.get("has_more"))

        return self._with_fallback(
            "博主作品", lambda: self._paged(page, limit),
            lambda: self.page_collect(f"{self.base}/user/{sec_uid}", API_POST,
                                      lambda b: [a for a in b.get("aweme_list") or [] if isinstance(a, dict)], limit),
            need_login=False)

    def detail(self, vid: str) -> Fetch:
        def api_way():
            self._pause()
            data = self.api(API_DETAIL, {"aweme_id": vid})
            aw = data.get("aweme_detail")
            return Fetch(items=[aw] if isinstance(aw, dict) else [], via="api")

        def page_way():
            items = self.page_collect(f"{self.base}/video/{vid}", API_DETAIL,
                                      lambda b: [b["aweme_detail"]] if isinstance(b.get("aweme_detail"), dict) else [],
                                      1, rounds=6, idle_rounds=4)
            return items

        return self._with_fallback("作品详情", api_way, page_way, need_login=False)

    def comments(self, vid: str, limit: int) -> list[dict]:
        """热门评论（接口默认按热度排序）。拿不到就返回空列表，不影响主流程"""
        if limit <= 0:
            return []
        self._pause()
        out: list[dict] = []
        try:
            cursor = 0
            while len(out) < limit:
                data = self.api(API_COMMENTS, {"aweme_id": vid, "cursor": str(cursor), "count": "20", "item_type": "0"})
                batch = [c for c in data.get("comments") or [] if isinstance(c, dict)]
                out.extend(batch)
                if not data.get("has_more") or not batch:
                    break
                cursor = data.get("cursor", 0)
                self._pause()
        except ApiFail:
            if not out:
                out = self.page_collect(f"{self.base}/video/{vid}", API_COMMENTS,
                                        lambda b: [c for c in b.get("comments") or [] if isinstance(c, dict)],
                                        limit, rounds=4, idle_rounds=3)
        out.sort(key=lambda c: int(c.get("digg_count") or 0), reverse=True)
        return out[:limit]

    def resolve(self, url: str) -> str:
        """短链接 → 真实地址"""
        self._goto(url)
        final = self.page.url
        if urlparse(final).netloc != urlparse(self.base).netloc:
            self._goto(self.base + "/")  # 页面内请求必须在抖音主站页面上发
        return final


# 通用名字：其他平台也用这个类（抖音专用的方法留在里面，互不影响）
Browser = Douyin
