@echo off
REM Image Hunter - 住宅/居住类 全量回填（可重复跑，去重不重抓）
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo [回填] 开始抓取 mooool 居住/示范区，请看下方进度...
echo ============================================================
python daily_hunt.py --backfill
echo ============================================================
echo [回填] 本轮结束。请把上面以 [时间] 开头的几行发给 Claude。
pause
