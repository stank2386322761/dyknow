@echo off
chcp 65001 >nul
cd /d "D:\BaiduSyncdisk\workcode\skills\DyKnow"
echo 🌙 夜间转录启动 — %date% %time%
.venv\Scripts\python.exe -m dyknow night --start 18:30 --end 08:30 --max-attempts 3
echo 🏁 夜间转录结束 — %date% %time%
