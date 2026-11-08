"""手机 / 云电脑登录：把抖音登录二维码存成图片，交给 AI 发到用户手机上扫。

适用于用户看不到浏览器窗口的情况（WorkBuddy 云电脑、豆包、服务器、手机上用 AI）。

流程：
  dyknow login            没有屏幕时自动走这里：后台开浏览器，二维码存成图片，立刻返回（退出码 5）
  dyknow login --wait     用户说扫好了之后运行，确认登录结果；二维码过期会自动刷新并提示重新发图
  dyknow login --code 123456   抖音扫码后要求短信验证时，把用户收到的验证码填进去

后台进程最多保持 10 分钟，登录成功或超时后自己退出；状态写在 <数据目录>/login/status.json。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import browser
from .config import Config

WORKER_SECONDS = 600

# 状态
STARTING = "starting"
QR_READY = "qr_ready"        # 二维码图片已生成，等用户扫
SCANNED = "scanned"          # 用户扫了，等手机上点确认
NEED_CODE = "need_code"      # 抖音要短信验证码
CAPTCHA = "captcha"          # 抖音要滑块验证（没法远程完成）
LOGGED_IN = "logged_in"
TIMEOUT = "timeout"
ERROR = "error"
FINAL = {LOGGED_IN, TIMEOUT, ERROR, CAPTCHA}

_FIND_QR_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 80 && r.height > 80 && s.visibility !== 'hidden' && s.display !== 'none'
      && Number(s.opacity) > 0.1 && r.bottom > 0 && r.right > 0; };
  let best = null, bestScore = -1;
  for (const el of document.querySelectorAll('img, canvas, svg')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect(), ratio = r.width / r.height;
    if (ratio < 0.85 || ratio > 1.18 || r.width > 520) continue;
    const src = el.tagName === 'IMG' ? (el.currentSrc || el.src || '') : '';
    const cls = String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className || '')
      + ' ' + String((el.parentElement && el.parentElement.className) || '');
    let score = 0;
    if (/qr/i.test(src) || /qr/i.test(cls)) score += 3;
    if (src.startsWith('data:image') || el.tagName !== 'IMG') score += 1;
    let p = el;
    for (let i = 0; p && i < 7; i++, p = p.parentElement) {
      if ((p.innerText || '').includes('扫码')) { score += 2; break; }
    }
    if (score > bestScore) { best = el; bestScore = score; }
  }
  if (!best || bestScore < 3) return null;
  const r = best.getBoundingClientRect();
  let sig = '';
  if (best.tagName === 'IMG') sig = (best.currentSrc || best.src || '').slice(-120);
  else if (best.tagName === 'CANVAS') { try { sig = best.toDataURL().slice(-120); } catch (e) { sig = 'canvas'; } }
  else sig = best.innerHTML.length + ':' + best.innerHTML.slice(-80);
  return {x: r.x, y: r.y, w: r.width, h: r.height, sig};
}"""

_TEXT_JS = "() => (document.body && document.body.innerText || '').slice(0, 6000)"

_CAPTCHA = ("拖动滑块", "请完成下列验证", "验证码中间页", "按顺序点击", "旋转图片")
_EXPIRED = ("二维码已失效", "二维码已过期", "二维码失效", "已失效", "已过期")
_SCANNED = ("扫码成功", "已扫码", "请在手机上确认", "在手机上确认", "确认登录")
_NEED_CODE = ("身份验证", "安全验证", "短信验证", "输入验证码", "请输入验证码", "接收短信")
_SEND_CODE = ["获取验证码", "发送验证码", "获取短信验证码"]
_SMS_OPTION = ["接收短信验证码", "短信验证码", "短信验证", "发送短信验证码", "手机短信验证"]
_WRONG_CODE = ("验证码错误", "验证码不正确", "验证码有误", "验证码已过期", "验证码已失效")
_SUBMIT = ["验证", "确定", "确认", "提交", "下一步", "登录"]


# ── 状态文件 ──────────────────────────────────


def state_dir(cfg: Config) -> Path:
    return cfg.home / "login"


def read_status(cfg: Config) -> dict:
    try:
        return json.loads((state_dir(cfg) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_status(cfg: Config, **st) -> None:
    d = state_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    st["updated"] = time.time()
    tmp = d / "status.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "status.json")


def mark_shown(cfg: Config, qr_version: int) -> None:
    """记下已经发给用户的是第几张二维码。--wait 跟它比，不跟「现在是第几张」比：
    二维码在发图之后、--wait 之前就过期换新了，也能把新图交给用户，而不是一直等一张作废的码。"""
    d = state_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    (d / "shown.json").write_text(json.dumps({"qr_version": int(qr_version or 0), "at": time.time()}), encoding="utf-8")


def shown_version(cfg: Config) -> int | None:
    try:
        return int(json.loads((state_dir(cfg) / "shown.json").read_text(encoding="utf-8"))["qr_version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # 已结束但还没被回收的子进程（僵尸）不算活着
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _stop_worker(cfg: Config) -> None:
    st = read_status(cfg)
    pid = st.get("pid")
    if not _alive(pid) or int(pid) == os.getpid():
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(int(pid), 15)
    except OSError:
        pass
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.2)


# ── 前台：启动 / 等待 / 填验证码 ─────────────────


def start(cfg: Config, force: bool = False, wait_qr: int = 90, phone: str = "") -> dict:
    """后台开浏览器去拿二维码，拿到（或已登录、或出错）就返回状态"""
    _stop_worker(cfg)
    d = state_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    for name in ("status.json", "qr.png", "page.png", "code.txt", "shown.json", "refresh.txt"):
        try:
            (d / name).unlink()
        except OSError:
            pass
    job = {"home": str(cfg.home), "values": cfg.values, "base_url": cfg.base_url, "force": force, "phone": phone}
    (d / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(d / "job.json", 0o600)
    except OSError:
        pass
    _write_status(cfg, state=STARTING)
    log = open(d / "worker.log", "a", encoding="utf-8")
    kwargs: dict = dict(stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(d))
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DYKNOW_HOME=str(cfg.home))
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.Popen([sys.executable, "-m", "dyknow", "login", "--worker", str(d / "job.json")],
                            env=env, **kwargs)
    log.close()
    deadline = time.time() + wait_qr
    while time.time() < deadline:
        st = read_status(cfg)
        if st.get("state") not in (None, STARTING):
            return st
        if proc.poll() is not None:
            time.sleep(0.5)
            st = read_status(cfg)
            if st.get("state") not in (None, STARTING):
                return st
            return {"state": ERROR, "reason": "worker_exit", "detail": f"后台登录进程意外退出，日志：{d / 'worker.log'}"}
        time.sleep(0.5)
    return {"state": ERROR, "reason": "slow", "detail": "90 秒内没能打开抖音登录页"}


def wait(cfg: Config, timeout: int = 240, seen_qr: int | None = None, no_scan: int = 40) -> dict:
    """等到登录成功、需要用户再做点什么（新二维码 / 验证码），或超时。

    用户说「扫好了」才会运行 --wait。如果过了 no_scan 秒抖音页面上还是「等待扫码」，
    说明他扫的那张图已经作废（最常见：发图到扫码之间二维码过期了），直接换一张新的交给用户，
    不再干等 90 秒、也不再让 AI 反复问「扫了没有」。
    """
    st = read_status(cfg)
    if not st:
        return {"state": ERROR, "reason": "not_started", "detail": "还没有开始登录"}
    if seen_qr is None:
        seen_qr = shown_version(cfg)
    start_qr = st.get("qr_version", 0) if seen_qr is None else seen_qr
    start_state = st.get("state")
    start_code = st.get("code_result")
    begun = time.time()
    deadline = begun + timeout
    refresh_asked = False
    while True:
        st = read_status(cfg)
        state = st.get("state")
        if state in FINAL:
            return st
        if state == NEED_CODE and (start_state != NEED_CODE or st.get("code_result") != start_code):
            return st
        if state in (QR_READY, SCANNED) and (st.get("qr_version", 0) != start_qr or start_state == NEED_CODE):
            st["qr_refreshed"] = True
            if refresh_asked:
                st["not_scanned"] = True
            return st
        if not _alive(st.get("pid")):
            st = read_status(cfg)
            if st.get("state") in FINAL:
                return st
            return {"state": ERROR, "reason": "worker_gone",
                    "detail": "后台登录进程已经不在了（可能被系统结束），需要重新开始"}
        if (not refresh_asked and no_scan and state == QR_READY and start_state in (QR_READY, SCANNED)
                and time.time() - begun >= no_scan
                and time.time() - float(st.get("scanned_at") or 0) > no_scan):
            # 等了这么久抖音还没看到扫码：让后台换一张新码
            (state_dir(cfg) / "refresh.txt").write_text("not_scanned", encoding="utf-8")
            refresh_asked = True
            deadline = max(deadline, time.time() + 25)
        if time.time() >= deadline:
            st["still_waiting"] = True
            return st
        time.sleep(1.5)


def submit_code(cfg: Config, code: str, timeout: int = 60) -> dict:
    st = read_status(cfg)
    if st.get("state") != NEED_CODE or not _alive(st.get("pid")):
        return {"state": st.get("state") or ERROR, "reason": "no_code_needed",
                "detail": "现在不需要验证码（或登录已结束）"}
    tries = st.get("code_try", 0)
    d = state_dir(cfg)
    (d / "code.txt").write_text(code.strip(), encoding="utf-8")
    deadline = time.time() + timeout
    while True:
        st = read_status(cfg)
        if st.get("state") in FINAL:
            return st
        if st.get("code_try", 0) > tries and st.get("code_result") in ("wrong", "no_input"):
            return st
        if st.get("state") in (QR_READY, SCANNED) and st.get("code_try", 0) > tries:
            return st
        if not _alive(st.get("pid")):
            return read_status(cfg) if read_status(cfg).get("state") in FINAL else \
                {"state": ERROR, "reason": "worker_gone", "detail": "后台登录进程已经不在了，需要重新开始"}
        if time.time() >= deadline:
            st["still_waiting"] = True
            return st
        time.sleep(1)


# ── 后台进程 ──────────────────────────────────


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
            dy.click_text(["扫码登录", "二维码登录"])
            dy.page.wait_for_timeout(1500)

    qr_version, qr_sig = 0, None
    qr_at, qr_life, scanned_at, last_page = 0.0, 0, 0.0, 0.0
    last_written: dict = {}
    code_result, sms_sent, code_try = "", mode == "phone", 0

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
        text = dy.eval(_TEXT_JS) or ""
        if time.time() - last_page > 8:
            # 随时留一张整页截图：卡住时 AI 能直接发给用户看，不用猜
            _shot(dy, d / "page.png")
            last_page = time.time()
        if any(k in text for k in _CAPTCHA):
            _shot(dy, d / "page.png")
            _write_status(cfg, **base, state=CAPTCHA, via=mode, page=str(d / "page.png"))
            return 1

        code_file = d / "code.txt"
        if code_file.exists():
            code = code_file.read_text(encoding="utf-8").strip()
            code_file.unlink()
            code_result = "filled" if _fill_code(dy, code) else "no_input"
            dy.page.wait_for_timeout(3000)
            if not dy.logged_in() and any(k in (dy.eval(_TEXT_JS) or "") for k in _WRONG_CODE):
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
            refresh_file.unlink()
            if not dy.click_text(["刷新", "点击刷新", "刷新二维码"], timeout=800):
                dy.page.reload()
                dy.page.wait_for_timeout(2500)
                dy.open_login_dialog()
            dy.page.wait_for_timeout(1500)
            qr_sig = None   # 下一张找到的码一律当新码发出去
            continue

        if qr_version and any(k in text for k in _NEED_CODE):
            # 扫码确认后抖音风控要「身份验证」：同一个会话里默认选短信，自动点发送。
            # 放在找二维码前面：验证框弹出来时，后面的登录框和二维码常常还在页面上
            if not sms_sent:
                dy.click_text(_SMS_OPTION, timeout=1500)
                sms_sent = bool(dy.click_text(_SEND_CODE, timeout=1500))
                _agree(dy)
            _shot(dy, d / "page.png")
            publish(NEED_CODE, code_sent=sms_sent, page=str(d / "page.png"), qr_version=qr_version)
            dy.page.wait_for_timeout(1500)
            continue

        qr = dy.eval(_FIND_QR_JS)
        scanned = any(k in text for k in _SCANNED)
        if qr and any(k in text for k in _EXPIRED):
            if qr_at and not qr_life:
                qr_life = int(time.time() - qr_at)   # 这台电脑上抖音二维码实际能撑多久
            if dy.click_text(["刷新", "点击刷新", "刷新二维码", "二维码已失效", "二维码已过期"]):
                dy.page.wait_for_timeout(1500)
                continue
            dy.open_login_dialog()
        if qr:
            if qr["sig"] != qr_sig and not (scanned and qr_sig):
                # 扫码后抖音会在码上盖一层「扫码成功」，图变了但不是新码，不能让用户重扫
                _shot(dy, d / "qr.png", qr)
                qr_sig = qr["sig"]
                qr_version += 1
                qr_at = time.time()
            if scanned and not scanned_at:
                scanned_at = time.time()
            elif not scanned:
                scanned_at = 0.0
            publish(SCANNED if scanned else QR_READY, qr=str(d / "qr.png"), qr_version=qr_version, qr_exact=True)
        else:
            dy.open_login_dialog()
            dy.page.wait_for_timeout(1500)
            if not dy.eval(_FIND_QR_JS) and not qr_version:
                # 实在找不到二维码：整页截图兜底（二维码在图里）
                _shot(dy, d / "qr.png")
                qr_version += 1
                qr_sig = "page"
                publish(QR_READY, qr=str(d / "qr.png"), qr_version=qr_version, qr_exact=False)
            continue
        dy.page.wait_for_timeout(1500)
    _write_status(cfg, **base, state=TIMEOUT, via=mode)
    return 1


def _send_phone_code(dy: "browser.Douyin", phone: str) -> str:
    """在登录框里切到「验证码登录」，填手机号，点「获取验证码」。返回 sent / captcha / no_form"""
    dy.click_text(["验证码登录", "手机号登录", "短信登录", "手机验证码登录"])
    dy.page.wait_for_timeout(800)
    found = dy.eval("""() => {
      const ok = el => { const r = el.getBoundingClientRect(); return r.width > 40 && r.height > 10
        && getComputedStyle(el).visibility !== 'hidden'; };
      const el = [...document.querySelectorAll('input')].filter(ok).find(el =>
        /手机号|手机号码|phone|mobile/i.test((el.placeholder || '') + (el.name || '') + (el.getAttribute('aria-label') || ''))
        || el.type === 'tel');
      if (!el) return false;
      el.setAttribute('data-dyknow-phone', '1');
      return true;
    }""")
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
    if not dy.click_text(_SEND_CODE):
        return "no_form"
    _agree(dy)  # 有的版本点了发送才弹「同意协议」
    dy.page.wait_for_timeout(2000)
    text = dy.eval(_TEXT_JS) or ""
    if any(k in text for k in _CAPTCHA):
        return "captcha"
    return "sent"


def _agree(dy: "browser.Douyin") -> None:
    """登录需要勾选用户协议：弹窗就点「同意」，没弹就勾上协议旁的选框（用户要求登录即视为同意）"""
    if dy.click_text(["同意并继续", "同意并登录", "同意并发送"], timeout=800):
        return
    dy.eval("""() => {
      for (const el of document.querySelectorAll('input[type=checkbox]')) {
        const t = (el.closest('label, div') || el).innerText || '';
        if (/协议|同意/.test(t) && !el.checked) { el.click(); }
      }
    }""")


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
    filled = dy.eval("""(code) => {
      const ok = el => { const r = el.getBoundingClientRect(); return r.width > 20 && r.height > 10
        && getComputedStyle(el).visibility !== 'hidden'; };
      const inputs = [...document.querySelectorAll('input')].filter(ok).filter(el =>
        /验证码|code/i.test((el.placeholder || '') + (el.name || '') + (el.getAttribute('aria-label') || ''))
        || ['tel', 'number'].includes(el.type) && Number(el.maxLength) > 0 && Number(el.maxLength) <= 8);
      const el = inputs[inputs.length - 1];
      if (!el) return false;
      el.setAttribute('data-dyknow-code', '1');
      return true;
    }""", code)
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
        clicked = dy.eval("""(names) => {
          let p = document.querySelector("[data-dyknow-code='1']");
          for (let i = 0; p && i < 8; i++, p = p.parentElement) {
            const btn = [...p.querySelectorAll('button, [role=button], div, span')].find(el =>
              names.includes((el.innerText || '').trim()) && el.getBoundingClientRect().width > 20);
            if (btn) { btn.click(); return true; }
          }
          return false;
        }""", _SUBMIT)
        if not clicked:
            dy.click_text(_SUBMIT)
    return True
