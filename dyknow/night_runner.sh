#!/bin/bash
# 夜间转录启动脚本
# 由定时任务触发，在 18:30-08:30 窗口内循环转录

cd "$(dirname "$0")/.." || exit 1

echo "🌙 夜间转录启动 — $(date '+%Y-%m-%d %H:%M:%S')"
.venv/Scripts/python.exe -m dyknow night --start 18:30 --end 08:30 --max-attempts 3
echo "🏁 夜间转录结束 — $(date '+%Y-%m-%d %H:%M:%S')"
