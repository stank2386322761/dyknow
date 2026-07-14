"""
CLI 入口 —— DyKnow
"""

import argparse
import asyncio
import sys
from pathlib import Path

from .config import config
from .db import get_db


def cmd_login(_args):
    from .login import login as do_login
    cookies = asyncio.run(do_login())
    return 0 if cookies else 1


def cmd_status(_args):
    db = get_db()
    from .login import load_cookies

    print("\nDyKnow 状态")
    print("=" * 50)
    print(f"登录: {'已登录' if load_cookies() else '未登录'}")
    print(f"笔记目录: {config.output_dir}")
    print(f"数据库:   {config.db_path}")
    print()
    print("各状态条目数:")
    for status_name, cnt in sorted(db.get_status_breakdown().items()):
        print(f"  {status_name:<22} {cnt:>5}")
    print(f"  {'总计':<22} {db.total_count():>5}")
    db.close()
    return 0


def cmd_sync(args):
    from .syncer import Syncer
    return Syncer().sync(
        count=args.count,
        only_index=args.only_index,
        only_transcribe=args.only_transcribe,
        transcribe=args.transcribe,
        full=args.full,
    )


def cmd_transcribe(args):
    from .syncer import Syncer
    return Syncer()._phase2_transcribe(
        count=args.count,
        include_failed=args.retry_failed,
        max_attempts=args.max_attempts,
    )


def cmd_reset(args):
    """
    把指定任务回退到 pending_index（强制重跑）。
    默认操作：把所有 status=transcribed 重置（重新转录）。
    """
    db = get_db()
    from .db import (
        STATUS_PENDING_INDEX, STATUS_TRANSCRIBED,
        STATUS_FAILED, TRANSCRIBE_PIPELINE_STATUSES,
    )

    target = args.target
    if target == "transcribed":
        rows = db.get_by_status(STATUS_TRANSCRIBED)
    elif target == "failed":
        rows = db.get_by_status(STATUS_FAILED)
    elif target == "all":
        placeholders = ",".join("?" * len(TRANSCRIBE_PIPELINE_STATUSES))
        rows = db.conn.execute(
            f"SELECT aweme_id, status FROM sync_log WHERE status IN ({placeholders})",
            tuple(TRANSCRIBE_PIPELINE_STATUSES),
        ).fetchall()
        rows = [dict(r) for r in rows]
    else:
        print(f"未知 target: {target}")
        return 1

    if not rows:
        print(f"没有 {target} 状态的任务")
        return 0

    if not args.yes:
        ans = input(f"将重置 {len(rows)} 条任务到 pending_index，确认？(y/N): ")
        if ans.strip().lower() != "y":
            print("已取消")
            return 0

    for r in rows:
        db.reset_to_pending_index(r["aweme_id"])
    print(f"✅ 已重置 {len(rows)} 条到 pending_index")
    return 0


def cmd_night(args):
    """夜间批量转录（时间窗口内循环运行）"""
    from .night_transcribe import main as night_main
    import sys
    # 模拟命令行参数
    sys.argv = [
        "night",
        "--start", args.start,
        "--end", args.end,
        "--max-attempts", str(args.max_attempts),
    ]
    return night_main()


def cmd_parse(args):
    from .parser import parse_and_generate
    output = Path(args.output) if args.output else None
    note_path = parse_and_generate(
        text=args.text,
        output_dir=output,
        transcribe_video=not args.no_transcribe,
    )
    if note_path:
        print(f"\nNOTE_PATH: {note_path}")
        return 0
    return 1


def main():
    parser = argparse.ArgumentParser(
        description="DyKnow — 抖音收藏自动化知识库工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
场景:
  A) 单视频    dyknow parse "<含抖音链接的文字>"
  B) 批量同步  dyknow sync
  C) 批量转录  dyknow transcribe        # 支持断点续转、Ctrl+C 安全退出

示例:
  dyknow login                    扫码登录
  dyknow status                   查看状态
  dyknow parse "<分享文案>"        单视频处理
  dyknow sync                     增量同步收藏
  dyknow sync --transcribe        同步+转录
  dyknow transcribe               断点续转
  dyknow transcribe --retry-failed 仅重试失败项
  dyknow transcribe --count 50    本次最多处理 50 条
  dyknow reset --target transcribed --yes  把已转录项全部重置
        """,
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("login", help="扫码登录")
    sub.add_parser("status", help="查看状态")

    sp = sub.add_parser("sync", help="批量同步收藏")
    sp.add_argument("--count", type=int, default=200)
    sp.add_argument("--only-index", action="store_true")
    sp.add_argument("--only-transcribe", action="store_true")
    sp.add_argument("--transcribe", action="store_true")
    sp.add_argument("--full", action="store_true")

    # 单独的 transcribe 子命令（断点续转主入口）
    tp = sub.add_parser(
        "transcribe",
        help="批量转录（支持断点续转、Ctrl+C 安全退出、失败重试）",
    )
    tp.add_argument(
        "--count", type=int, default=0,
        help="本次最多处理的条目数，0=全部（默认 0）",
    )
    tp.add_argument(
        "--retry-failed", action="store_true",
        help="把 failed 状态的任务也纳入重试队列",
    )
    tp.add_argument(
        "--max-attempts", type=int, default=3,
        help="单条任务最大尝试次数，超过则跳过（默认 3）",
    )

    # reset 子命令
    rp = sub.add_parser(
        "reset",
        help="把任务重置到 pending_index（强制重跑）",
    )
    rp.add_argument(
        "--target",
        choices=["transcribed", "failed", "all"],
        default="transcribed",
        help="重置哪类任务（默认 transcribed）",
    )
    rp.add_argument("--yes", "-y", action="store_true", help="跳过确认")

    # night 子命令
    np = sub.add_parser(
        "night",
        help="夜间批量转录（时间窗口内循环运行，超时自动停止）",
    )
    np.add_argument("--start", default="18:30", help="窗口开始时间（默认 18:30）")
    np.add_argument("--end", default="08:30", help="窗口结束时间（默认 08:30）")
    np.add_argument("--max-attempts", type=int, default=3, help="单条最大重试次数")

    pp = sub.add_parser("parse", help="单视频解析")
    pp.add_argument("text", help="包含抖音链接的文本")
    pp.add_argument("--output", "-o", default=None)
    pp.add_argument("--no-transcribe", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "login": cmd_login,
        "status": cmd_status,
        "sync": cmd_sync,
        "transcribe": cmd_transcribe,
        "reset": cmd_reset,
        "parse": cmd_parse,
        "night": cmd_night,
    }
    handler = handlers.get(args.command)
    return handler(args) if handler else 1


if __name__ == "__main__":
    sys.exit(main())
