@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/6] 清理损坏的 .git 目录...
if exist .git (
    rmdir /s /q .git
)

echo [2/6] 初始化 git 仓库...
git init
git config user.name "G爸"
git config user.email "lxguanyu@gmail.com"
git branch -M main

echo [3/6] 暂存所有文件...
git add .

echo [4/6] 创建初始提交...
git commit -m "feat: 地产数据决策引擎 DDS 初始版本"

echo [5/6] 绑定远程仓库...
git remote add origin https://github.com/KODxixi/dds.git

echo [6/6] 推送到 GitHub（可能需要输入 GitHub 凭据）...
git push -u origin main

echo.
echo ===== 完成！=====
echo 仓库地址: https://github.com/KODxixi/dds
pause
