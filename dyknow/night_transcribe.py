"""
夜间批量转录 —— 在指定时间窗口内持续转录，超时自动停止

用法:
    python -m dyknow.night_transcribe          # 使用默认窗口 18:30-08:30
    python -m dyknow.night_transcribe --end 08:30 --start 18:30
"""

import sys
import time
import signal
import argparse
from datetime import datetime, time as dt_time
from pathlib import Path

from .db import (
    get_db,
    STATUS_PENDING_INDEX,
    STATUS_PENDING_DOWNLOAD,
    STATUS_PENDING_AUDIO,
    STATUS_PENDING_TRANSCRIBE,
    STATUS_FAILED,
)
from .syncer import Syncer

PENDING_STATUSES = {
    STATUS_PENDING_INDEX,
    STATUS_PENDING_DOWNLOAD,
    STATUS_PENDING_AUDIO,
    STATUS_PENDING_TRANSCRIBE,
    STATUS_FAILED,
}

# 全局停止标志
_stop_requested = False


def signal_handler(signum, frame):
    global _stop_requested
    print("\n⚠️  收到停止信号，当前视频处理完后将退出...")
    _stop_requested = True


def parse_time(s: str) -> dt_time:
    """解析 HH:MM 格式的时间"""
    parts = s.strip().split(":")
    return dt_time(int(parts[0]), int(parts[1]))


def is_in_window(start: dt_time, end: dt_time) -> bool:
    """
    判断当前是否在允许的运行窗口内。
    支持跨夜窗口（如 18:30 ~ 08:30）。
    """
    now = datetime.now().time()
    if start <= end:
        # 同一天内: start <= now <= end
        return start <= now <= end
    else:
        # 跨夜: now >= start OR now <= end
        return now >= start or now <= end


def pending_count(db) -> int:
    """统计所有待处理条目数"""
    total = 0
    for s in PENDING_STATUSES:
        total += db.count_by_status(s)
    return total


def main():
    parser = argparse.ArgumentParser(description="夜间批量转录")
    parser.add_argument("--start", default="18:30", help="窗口开始时间 (默认 18:30)")
    parser.add_argument("--end", default="08:30", help="窗口结束时间 (默认 08:30)")
    parser.add_argument("--max-attempts", type=int, default=3, help="单条最大重试次数")
    args = parser.parse_args()

    start_time = parse_time(args.start)
    end_time = parse_time(args.end)

    print(f"🌙 夜间转录模式")
    print(f"   窗口: {args.start} ~ {args.end}")
    print(f"   最大重试: {args.max_attempts} 次/条")
    print()

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    db = get_db()
    syncer = Syncer()

    total_success = 0
    total_failed = 0
    total_skipped = 0
    rounds = 0

    try:
        while not _stop_requested:
            # 检查是否还在窗口内
            if not is_in_window(start_time, end_time):
                now_str = datetime.now().strftime("%H:%M")
                print(f"⏰ {now_str} — 已超出运行窗口，停止转录")
                break

            # 统计剩余
            remaining = pending_count(db)
            if remaining == 0:
                print("✅ 所有内容已转录完毕！")
                break

            rounds += 1
            now_str = datetime.now().strftime("%H:%M")
            print(f"\n{'='*50}")
            print(f"🔄 第 {rounds} 轮 — {now_str} — 剩余 {remaining} 条")
            print(f"{'='*50}")

            # 运行转录（包含 failed 重试）
            try:
                result = syncer._phase2_transcribe(
                    count=remaining + 10,  # 多取一些以防新加入
                    include_failed=True,
                    max_attempts=args.max_attempts,
                )
                # _phase2_transcribe 返回 int (0=成功)
            except Exception as e:
                print(f"❌ 转录轮次异常: {e}")
                # 短暂等待后继续
                time.sleep(30)
                continue

            # 读取本轮统计
            db2 = get_db()
            remaining_after = pending_count(db2)
            processed = remaining - remaining_after
            print(f"\n📊 本轮处理: ~{processed} 条 | 剩余: {remaining_after} 条")

            if processed <= 0 and remaining_after > 0:
                # 可能全部失败了，等待一下再重试
                print("⏳ 本轮无进展，等待 60s 后重试...")
                time.sleep(60)

            db2.close()

    except KeyboardInterrupt:
        print("\n⚠️  用户中断")
    finally:
        db2 = get_db()
        final_remaining = pending_count(db2)
        db2.close()
        print(f"\n{'='*50}")
        print(f"🌙 夜间转录结束")
        print(f"   总轮次: {rounds}")
        print(f"   剩余: {final_remaining} 条")
        print(f"{'='*50}")

    return 0 if final_remaining == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
