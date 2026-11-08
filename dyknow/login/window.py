"""有屏幕时的登录：弹出浏览器，用户直接用抖音 App 扫屏幕上的码。

返回和 session 一样的状态字典，命令行用同一套逻辑翻译给 AI。
"""

from __future__ import annotations

from typing import Callable

from .. import browser
from ..config import Config
from .session import LOGGED_IN, TIMEOUT

WAIT_SECONDS = 300


def login_with_window(cfg: Config, force: bool = False, log: Callable[[str], None] = print) -> dict:
    """浏览器出错时抛 browser.BrowserError，由调用方处理"""
    with browser.Douyin(cfg, headless=False, log=log) as dy:
        if dy.logged_in() and not force:
            try:
                dy.api(browser.API_COLLECTION, method="POST", body={"count": "1", "cursor": "0"})
                return {"state": LOGGED_IN, "already": True}
            except browser.ApiFail as e:
                if e.reason != browser.LOGIN:
                    # 本机有登录状态，只是暂时没法向抖音确认，不影响使用
                    return {"state": LOGGED_IN, "already": True, "unverified": True}
                log("登录已在抖音那边过期，需要重新扫码。")
                force = True
        if force:
            dy.ctx.clear_cookies()
            dy.page.reload()
            dy.page.wait_for_timeout(2000)
        if dy.page_state() == "captcha":
            log("🧩 页面上有验证码，请先在浏览器里手动完成验证。")
        dy.open_login_dialog()
        log(f"📱 浏览器已打开：如果没有自动弹出登录框，点页面右上角「登录」，用抖音 App 扫码（最多等 {WAIT_SECONDS // 60} 分钟）…")
        if not dy.wait_for_login(WAIT_SECONDS):
            return {"state": TIMEOUT, "window": True}
        return {"state": LOGGED_IN}
