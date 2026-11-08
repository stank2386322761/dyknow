"""抖音登录模块。

分三层，各管各的：
  rules.py    抖音登录页长什么样（文案关键词、找二维码的脚本）——抖音改版只改这里
  session.py  登录状态和前台命令：开始、等结果、交验证码；不碰浏览器，可以单独测试
  worker.py   后台浏览器进程：按 rules 认页面，把变化写成 session 的状态
  window.py   有屏幕时直接弹浏览器扫码
命令行（cli.py）只负责把这里返回的状态翻译成给 AI 的「下一步」。
"""

from .session import (CAPTCHA, ERROR, FINAL, LOGGED_IN, NEED_CODE, QR_READY, QR_EXPIRED, SCANNED, STARTING, TIMEOUT,
                      WORKER_SECONDS, mark_shown, read_status, shown_version, start, state_dir, stop_worker,
                      submit_code, wait, refresh_qr)
from .window import login_with_window


def run_worker(job_path: str, log=print) -> int:
    from .worker import run_worker as _run
    return _run(job_path, log=log)


__all__ = ["CAPTCHA", "ERROR", "FINAL", "LOGGED_IN", "NEED_CODE", "QR_READY", "SCANNED", "STARTING", "TIMEOUT",
           "WORKER_SECONDS", "mark_shown", "read_status", "shown_version", "start", "state_dir", "stop_worker",
           "submit_code", "wait", "login_with_window", "run_worker"]
