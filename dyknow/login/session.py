"""登录会话：状态文件、启动后台登录进程、等结果、交验证码。

这一层不打开浏览器，也不认识抖音页面：浏览器在后台进程（worker.py）里，
两边只通过 <数据目录>/login/ 下的文件说话：
  status.json  后台写的当前状态（下面的 STARTING … ERROR）
  shown.json   已经发给用户的是第几张二维码
  code.txt     用户报的验证码（前台写，后台读完删）
  refresh.txt  请后台换一张新二维码（前台写，后台读完删）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from ..config import Config

WORKER_SECONDS = 600

# 状态
STARTING = "starting"
QR_READY = "qr_ready"        # 二维码图片已生成，等用户扫
QR_EXPIRED = "qr_expired"
SCANNED = "scanned"          # 用户扫了，等手机上点确认
NEED_CODE = "need_code"      # 抖音要短信验证码
CAPTCHA = "captcha"          # 抖音要滑块验证（没法远程完成）
LOGGED_IN = "logged_in"
TIMEOUT = "timeout"
ERROR = "error"
FINAL = {LOGGED_IN, TIMEOUT, ERROR, CAPTCHA}


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


def _settle_success(st: dict, timeout: float = 15) -> dict:
    """成功必须等后台进程退出，确保浏览器配置目录的锁已经释放。"""
    if st.get("state") != LOGGED_IN or not st.get("pid") or int(st["pid"]) == os.getpid():
        return st
    deadline = time.monotonic() + timeout
    while _alive(st["pid"]):
        if time.monotonic() >= deadline:
            return {"state": ERROR, "reason": "worker_busy", "detail": "登录成功，但浏览器尚未关闭，请稍后重试"}
        time.sleep(0.2)
    return st


def stop_worker(cfg: Config) -> None:
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
    stop_worker(cfg)
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
    pkg_root = str(Path(__file__).resolve().parents[2])   # dyknow/login/session.py → 项目根目录
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.Popen([sys.executable, "-m", "dyknow", "login", "--worker", str(d / "job.json")],
                            env=env, **kwargs)
    log.close()
    deadline = time.time() + wait_qr
    while time.time() < deadline:
        st = read_status(cfg)
        if st.get("state") not in (None, STARTING):
            return _settle_success(st)
        if proc.poll() is not None:
            time.sleep(0.5)
            st = read_status(cfg)
            if st.get("state") not in (None, STARTING):
                return _settle_success(st)
            return {"state": ERROR, "reason": "worker_exit", "detail": f"后台登录进程意外退出，日志：{d / 'worker.log'}"}
        time.sleep(0.5)
    return {"state": ERROR, "reason": "slow", "detail": "90 秒内没能打开抖音登录页"}


def wait(cfg: Config, timeout: int = 240, seen_qr: int | None = None, no_scan: int = 40) -> dict:
    """等到登录成功、需要用户再做点什么（新二维码 / 验证码），或超时。

    用户说「扫好了」才运行 --wait。未收到扫码时保持会话，等待用户决定是否刷新。
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
        if state in FINAL or state == QR_EXPIRED:
            return _settle_success(st)
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
            st["still_waiting"] = True
            return st
        if time.time() >= deadline:
            st["still_waiting"] = True
            return st
        time.sleep(1.5)


def refresh_qr(cfg: Config, timeout: int = 35) -> dict:
    """请求后台重新生成二维码，等对应请求完成后再返回图片。"""
    st = read_status(cfg)
    if st.get("state") not in (QR_READY, QR_EXPIRED):
        return st or {"state": ERROR, "reason": "not_started", "detail": "还没有开始登录"}
    token = uuid.uuid4().hex
    (state_dir(cfg) / "refresh.txt").write_text(token, encoding="utf-8")
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = read_status(cfg)
        if st.get("state") in FINAL or st.get("state") == NEED_CODE:
            return _settle_success(st)
        if st.get("refresh_token") == token and st.get("state") == QR_READY:
            return {**st, "qr_refreshed": True}
        if not _alive(st.get("pid")):
            return {"state": ERROR, "reason": "worker_gone", "detail": "后台登录进程已结束，请重新登录"}
        time.sleep(0.5)
    return {"state": ERROR, "reason": "refresh_timeout", "detail": "未能确认新二维码已生成，请重新登录"}


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
            return _settle_success(st)
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
