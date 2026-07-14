"""允许通过 python -m dyknow 运行"""
import sys
import os

# Windows 下强制 UTF-8 输出，解决中文和 emoji 显示问题
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # 也设置环境变量，让子进程也能 UTF-8
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from .main import main

if __name__ == "__main__":
    sys.exit(main())
