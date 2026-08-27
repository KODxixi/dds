@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 检查 Pillow...
python -c "import PIL" 2>nul || python -m pip install pillow
if errorlevel 1 (echo Pillow 安装失败，请手动 pip install pillow & pause & exit /b)
echo [2/2] 开始转换...
python backfill_png.py %*
echo.
echo ====== 转换结束，按任意键关闭 ======
pause >nul
