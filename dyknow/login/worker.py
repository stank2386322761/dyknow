"""后台登录进程：持有浏览器，把抖音登录页的变化翻译成 session 里的状态。

由 `dyknow login --worker <job.json>` 启动（session.start 负责启动），最多活 WORKER_SECONDS 秒。
认页面只用 rules.py 里的规则。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable

from .. import browser
from ..config import Config
from . import rules
from .session import (CAPTCHA, ERROR, LOGGED_IN, NEED_CODE, QR_READY, QR_EXPIRED, SCANNED, STARTING, TIMEOUT,
                      WORKER_SECONDS, _write_status, state_dir)

def run_worker(job_path: str, log: Callable[[str], None] = print) -> int:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    try:
        Path(job_path).unlink()   # 里面有手机号，读完就删
    except OSError:
        pass
    cfg = Config(home=Path(job["home"]), values=job.get("values") or {})
    cfg.base_url = job.get("base_url") or cfg.base_url
    base = dict(pid=os.getpid())
    _write_status(cfg, **base, state=STARTING)
    try:
        dy = browser.Douyin(cfg, headless=False, log=log)
        dy.scale = 2
        with dy:
            return _work(cfg, dy, bool(job.get("force")), base, log, phone=str(job.get("phone") or ""))
    except browser.BrowserError as e:
        _write_status(cfg, **base, state=ERROR, reason=e.reason,
                      detail=browser.REASON_TEXT.get(e.reason, e.reason) + (f"（{e.detail}）" if e.detail else ""))
        return 1
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0][:150] if str(e) else type(e).__name__
        _write_status(cfg, **base, state=ERROR, reason="error", detail=msg)
        return 1


def _work(cfg: Config, dy: "browser.Douyin", force: bool, base: dict, log, phone: str = "") -> int:
    d = state_dir(cfg)
    if dy.logged_in() and not force:
        try:
            dy.api(browser.API_COLLECTION, method="POST", body={"count": "1", "cursor": "0"})
            _write_status(cfg, **base, state=LOGGED_IN, already=True)
            return 0
        except browser.ApiFail as e:
            if e.reason != browser.LOGIN:
                _write_status(cfg, **base, state=LOGGED_IN, already=True)
                return 0
            force = True
    if force:
        dy.ctx.clear_cookies()
        dy.page.reload()
        dy.page.wait_for_timeout(2000)

    deadline = time.time() + WORKER_SECONDS
    dy.open_login_dialog()
    dy.page.wait_for_timeout(1500)

    mode, fallback = "qr", ""
    if phone:
        sent = _send_phone_code(dy, phone)
        if sent == "sent":
            mode = "phone"
        else:
            # 抖音要滑块，或者找不到手机号输入框：同一个浏览器里直接换成扫码，不用用户重来
            fallback = sent
            _close_overlays(dy)
            dy.click_text(rules.SWITCH_TO_QR)
            dy.page.wait_for_timeout(1500)

    qr_version, qr_sig = 0, None
    session_id = uuid.uuid4().hex[:12]
    qr_path = d / f"qr-{session_id}-page.png"
    qr_at, qr_life, scanned_at, last_page = 0.0, 0, 0.0, 0.0
    last_written: dict = {}
    code_result, sms_sent, code_try = "", mode == "phone", 0
    refresh_token = ""
    refresh_from_sig = None

    def publish(state: str, **extra) -> None:
        key = {"state": state, "qr_version": qr_version, "code_result": code_result, "code_try": code_try}
        if key != last_written:
            last_written.clear()
            last_written.update(key)
            _write_status(cfg, **base, state=state, via=mode, fallback=fallback, expires_at=deadline,
                          code_result=code_result, code_try=code_try, qr_at=qr_at, qr_life=qr_life,
                          scanned_at=scanned_at, **{"page": str(d / "page.png"), **extra})

    while time.time() < deadline:
        if dy.logged_in():
            dy.page.wait_for_timeout(3000)
            _write_status(cfg, **base, state=LOGGED_IN, via=mode)
            return 0
        text = dy.eval(rules.TEXT_JS) or ""
        if time.time() - last_page > 8:
            # 随时留一张整页截图：卡住时 AI 能直接发给用户看，不用猜
            _shot(dy, d / "page.png")
            last_page = time.time()
        if any(k in text for k in rules.CAPTCHA):
            _shot(dy, d / "page.png")
            _write_status(cfg, **base, state=CAPTCHA, via=mode, page=str(d / "page.png"))
            return 1

        code_file = d / "code.txt"
        if code_file.exists():
            code = code_file.read_text(encoding="utf-8").strip()
            code_file.unlink()
            code_result = "filled" if _fill_code(dy, code) else "no_input"
            dy.page.wait_for_timeout(3000)
            if not dy.logged_in() and any(k in (dy.eval(rules.TEXT_JS) or "") for k in rules.WRONG_CODE):
                code_result = "wrong"
            code_try += 1
            continue

        if mode == "phone":
            # 手机号登录：等用户报验证码；验证码过期了允许再发一次
            _shot(dy, d / "page.png")
            publish(NEED_CODE, code_sent=True, page=str(d / "page.png"))
            dy.page.wait_for_timeout(1500)
            continue

        refresh_file = d / "refresh.txt"
        if refresh_file.exists():
            # 用户说扫了、抖音却没收到：换一张全新的二维码
            refresh_token = refresh_file.read_text(encoding="utf-8")
            refresh_from_sig = qr_sig
            refresh_file.unlink()
            if not dy.click_text(rules.REFRESH, timeout=800):
                dy.page.reload()
                dy.page.wait_for_timeout(2500)
                dy.open_login_dialog()
            dy.page.wait_for_timeout(1500)
            continue

        if qr_version and any(k in text for k in rules.NEED_CODE):
            # 扫码确认后抖音风控要「身份验证」：同一个会话里默认选短信，自动点发送。
            # 放在找二维码前面：验证框弹出来时，后面的登录框和二维码常常还在页面上
            if not sms_sent:
                dy.click_text(rules.SMS_OPTION, timeout=1500)
                sms_sent = bool(dy.click_text(rules.SEND_CODE, timeout=1500))
                _agree(dy)
            _shot(dy, d / "page.png")
            publish(NEED_CODE, code_sent=sms_sent, page=str(d / "page.png"), qr_version=qr_version)
            dy.page.wait_for_timeout(1500)
            continue

        qr = dy.eval(rules.FIND_QR_JS)
        scanned = any(k in text for k in rules.SCANNED)
        if qr and any(k in text for k in rules.EXPIRED):
            if qr_at and not qr_life:
                qr_life = int(time.time() - qr_at)   # 这台电脑上抖音二维码实际能撑多久
            publish(QR_EXPIRED, qr=str(qr_path), qr_version=qr_version, qr_exact=True)
            dy.page.wait_for_timeout(1500)
            continue
        if qr:
            if qr_sig == "page" and not refresh_token:
                # 整页截图已经发出：识别出其中的二维码不代表换码。
                qr_sig = qr["sig"]
            if qr_sig is not None and qr["sig"] != qr_sig and not refresh_token and not scanned:
                publish(QR_EXPIRED, qr=str(qr_path), qr_version=qr_version, qr_exact=True)
                dy.page.wait_for_timeout(1500)
                continue
            if refresh_token and refresh_from_sig is not None and qr["sig"] == refresh_from_sig:
                dy.page.wait_for_timeout(1500)
                continue
            if qr["sig"] != qr_sig and not (scanned and qr_sig):
                # 扫码后抖音会在码上盖一层遮罩，图变了但不是新码，不能让用户重扫
                qr_path = d / f"qr-{session_id}-{qr_version + 1}.png"
                _shot(dy, qr_path, qr)
                qr_sig = qr["sig"]
                qr_version += 1
                qr_at = time.time()
            if scanned and not scanned_at:
                scanned_at = time.time()
            elif not scanned:
                scanned_at = 0.0
            publish(SCANNED if scanned else QR_READY, qr=str(qr_path), qr_version=qr_version, qr_exact=True, refresh_token=refresh_token)
            refresh_from_sig = None
            refresh_token = ""
        else:
            if qr_version:
                dy.page.wait_for_timeout(1500)
                continue
            dy.open_login_dialog()
            dy.page.wait_for_timeout(1500)
            if not dy.eval(rules.FIND_QR_JS) and not qr_version:
                # 实在找不到二维码：整页截图兜底（二维码在图里）
                _shot(dy, qr_path)
                qr_version += 1
                qr_sig = "page"
                publish(QR_READY, qr=str(qr_path), qr_version=qr_version, qr_exact=False)
            continue
        dy.page.wait_for_timeout(1500)
    _write_status(cfg, **base, state=TIMEOUT, via=mode)
    return 1


def _send_phone_code(dy: "browser.Douyin", phone: str) -> str:
    """在登录框里切到「验证码登录」，填手机号，点「获取验证码」。返回 sent / captcha / no_form"""
    dy.click_text(rules.SWITCH_TO_SMS)
    dy.page.wait_for_timeout(800)
    found = dy.eval(rules.PHONE_INPUT_JS)
    if not found:
        return "no_form"
    try:
        box = dy.page.locator("[data-dyknow-phone='1']").first
        box.click(timeout=3000)
        box.fill("")
        box.type(phone, delay=60)
    except Exception:  # noqa: BLE001
        return "no_form"
    _agree(dy)
    if not dy.click_text(rules.SEND_CODE):
        return "no_form"
    _agree(dy)  # 有的版本点了发送才弹「同意协议」
    dy.page.wait_for_timeout(2000)
    text = dy.eval(rules.TEXT_JS) or ""
    if any(k in text for k in rules.CAPTCHA):
        return "captcha"
    return "sent"


def _agree(dy: "browser.Douyin") -> None:
    """登录需要勾选用户协议：弹窗就点「同意」，没弹就勾上协议旁的选框（用户要求登录即视为同意）"""
    if dy.click_text(rules.AGREE, timeout=800):
        return
    dy.eval(rules.AGREE_CHECKBOX_JS)


def _close_overlays(dy: "browser.Douyin") -> None:
    try:
        dy.page.keyboard.press("Escape")
        dy.page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass


def _shot(dy: "browser.Douyin", path: Path, box: dict | None = None) -> None:
    try:
        if box:
            pad = 18
            vw = dy.page.viewport_size or {"width": 1280, "height": 860}
            x, y = max(0, box["x"] - pad), max(0, box["y"] - pad)
            w = min(box["w"] + pad * 2, vw["width"] - x)
            h = min(box["h"] + pad * 2, vw["height"] - y)
            dy.page.screenshot(path=str(path), clip={"x": x, "y": y, "width": w, "height": h})
        else:
            dy.page.screenshot(path=str(path))
    except Exception:  # noqa: BLE001
        pass


def _fill_code(dy: "browser.Douyin", code: str) -> bool:
    filled = dy.eval(rules.CODE_INPUT_JS, code)
    if not filled:
        return False
    try:
        box = dy.page.locator("[data-dyknow-code='1']").first
        box.click(timeout=3000)
        box.fill("")
        box.type(code, delay=80)
    except Exception:  # noqa: BLE001
        return False
    dy.page.wait_for_timeout(800)
    try:
        box.press("Enter")
        dy.page.wait_for_timeout(1500)
    except Exception:  # noqa: BLE001
        pass
    if not dy.logged_in():
        # 先找验证码输入框附近的提交按钮（页面顶部也有个「登录」，不能点错）
        clicked = dy.eval(rules.SUBMIT_NEAR_CODE_JS, rules.SUBMIT)
        if not clicked:
            dy.click_text(rules.SUBMIT)
    return True
