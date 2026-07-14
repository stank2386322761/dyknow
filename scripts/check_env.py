#!/usr/bin/env python3
"""
DyKnow 环境检测脚本
检查所有依赖是否就绪，给出明确的修复指引。
首次使用前运行: python scripts/check_env.py
"""

import sys
import subprocess
from pathlib import Path

# 确保能导入 dyknow
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PASS = "  [OK]"
FAIL = "  [FAIL]"
WARN = "  [WARN]"
INFO = "  [INFO]"


def check(desc: str, ok: bool, fix_hint: str = "") -> bool:
    """输出单条检测结果"""
    if ok:
        print(f"{PASS} {desc}")
    else:
        print(f"{FAIL} {desc}")
        if fix_hint:
            print(f"       修复: {fix_hint}")
    return ok


def main():
    print("=" * 55)
    print("  DyKnow 环境检测")
    print("=" * 55)
    print()

    all_ok = True

    # ── 1. Python 版本 ──
    print("── Python ──")
    py_ver = sys.version_info
    ok = py_ver >= (3, 11)
    all_ok &= check(
        f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}",
        ok,
        "安装 Python 3.11+: https://www.python.org/downloads/",
    )

    # ── 2. pip 包 ──
    print("\n── 依赖包 ──")
    required = {
        "requests": "pip install requests",
        "playwright": "pip install playwright",
        "pywhispercpp": "pip install pywhispercpp",
        "av": "pip install av",
    }
    for pkg, fix in required.items():
        try:
            import importlib.util
            found = importlib.util.find_spec(pkg) is not None
        except Exception:
            found = False
        all_ok &= check(pkg, found, fix)

    # ── 3. Playwright 浏览器 ──
    print("\n── Playwright 浏览器 ──")
    import os
    import platform
    chromium_paths = []
    if platform.system() == "Windows":
        local_appdata = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        chromium_paths = list(Path(local_appdata).glob("ms-playwright/chromium-*/chrome-win*/chrome.exe"))
    else:
        chromium_paths = list(Path.home().glob(".cache/ms-playwright/chromium-*/chrome"))
        if not chromium_paths:
            chromium_paths = list(Path.home().glob("**/ms-playwright/chromium-*/chrome*"))

    if chromium_paths:
        all_ok &= check(f"Chromium 浏览器 ({chromium_paths[0]})", True)
    else:
        all_ok &= check(
            "Chromium 浏览器",
            False,
            "playwright install chromium",
        )

    # ── 4. 音频工具 ──
    print("\n── 音频提取 ──")
    has_ffmpeg = False
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        has_ffmpeg = True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    has_av = False
    try:
        import importlib.util
        has_av = importlib.util.find_spec("av") is not None
    except Exception:
        pass

    # ffmpeg 或 av 至少有一个即可
    if has_ffmpeg:
        check("ffmpeg (推荐)", True)
    else:
        check(
            "ffmpeg (推荐) — 未安装，将使用 av 库替代",
            bool(has_av),
            "安装 ffmpeg 并添加到 PATH: https://ffmpeg.org/download.html",
        )
    check("av 库 (备选)", has_av, "pip install av")

    # ── 5. 转录模型 ──
    print("\n── 转录模型 ──")
    models_dir = PROJECT_ROOT / "data" / "models"
    tiny_model = models_dir / "ggml-tiny.bin"
    small_model = models_dir / "ggml-small.bin"

    has_tiny = tiny_model.exists()
    has_small = small_model.exists()

    if has_tiny:
        size_mb = tiny_model.stat().st_size / (1024 * 1024)
        all_ok &= check(f"ggml-tiny.bin ({size_mb:.0f} MB)", True)
    else:
        all_ok &= check(
            "ggml-tiny.bin",
            False,
            "下载: https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin → 放入 data/models/",
        )

    if has_small:
        size_mb = small_model.stat().st_size / (1024 * 1024)
        check(f"ggml-small.bin ({size_mb:.0f} MB)", True)
    else:
        check(
            "ggml-small.bin (可选)",
            False,
            "下载: https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin → 放入 data/models/",
        )

    # ── 6. 转录功能实测 ──
    print("\n── 转录功能实测 ──")
    if has_tiny and (has_ffmpeg or has_av):
        try:
            from dyknow.transcriber import is_available, get_active_backend
            if is_available():
                backend = get_active_backend()
                all_ok &= check(f"转录后端就绪 ({backend})", True)
            else:
                all_ok &= check("转录后端就绪", False, "请检查 pywhispercpp 和 ffmpeg/av 是否正确安装")
        except Exception as e:
            all_ok &= check("转录模块加载", False, str(e))
    else:
        check("转录功能实测", False, "需模型文件和音频工具就绪后才能测试")

    # ── 7. 数据库 ──
    print("\n── 数据库 ──")
    db_path = PROJECT_ROOT / "data" / "dyknow.db"
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        check(f"dyknow.db ({size_mb:.1f} MB)", True)
    else:
        check("dyknow.db", True)  # 首次使用会自动创建

    # ── 8. 输出目录 ──
    print("\n── 输出目录 ──")
    output_dir = PROJECT_ROOT / "抖音收藏"
    if output_dir.exists():
        md_count = len(list(output_dir.rglob("*.md")))
        check(f"抖音收藏/ ({md_count} 条笔记)", True)
    else:
        check("抖音收藏/ (首次使用会自动创建)", True)

    # ── 汇总 ──
    print()
    print("=" * 55)
    if all_ok:
        print("  [OK] All checks passed. DyKnow is ready!")
    else:
        print("  [WARN] Some checks failed. See hints above.")
    print("=" * 55)
    print()
    print("快速开始:")
    print("  python -m dyknow login            # 扫码登录抖音")
    print("  python -m dyknow parse \"<分享文案>\"  # 单视频解析")
    print("  python -m dyknow sync             # 同步收藏")
    print("  python -m dyknow sync --transcribe  # 同步+转录")
    print("  python -m dyknow status           # 查看状态")
    print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
