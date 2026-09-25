"""
运行 DyKnow 全部测试

用法:
    .venv/Scripts/python.exe tests/run_all.py
    .venv/Scripts/python.exe -m pytest tests/ -v
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    suite = unittest.TestLoader().discover(
        str(HERE), pattern="test_*.py", top_level_dir=str(HERE)
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)
    print("\n" + "=" * 60)
    print(f"通过: {total - failed - skipped} | 失败: {failed} | 跳过: {skipped} | 合计: {total}")
    print("=" * 60)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
