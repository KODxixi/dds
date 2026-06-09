Set-Location $PSScriptRoot
$ErrorActionPreference = "Stop"

Write-Host "[1/6] Removing old .git..." -ForegroundColor Cyan
if (Test-Path ".git") {
    Get-ChildItem -Path ".git" -Recurse -Force | ForEach-Object {
        $_.Attributes = "Normal"
    }
    Remove-Item -Path ".git" -Recurse -Force
    Write-Host "      Done." -ForegroundColor Green
}

Write-Host "[2/6] git init..." -ForegroundColor Cyan
git init
git config user.name "G"
git config user.email "lxguanyu@gmail.com"
git branch -M main

Write-Host "[3/6] git add..." -ForegroundColor Cyan
git add .

Write-Host "[4/6] git commit..." -ForegroundColor Cyan
git commit -m "feat: DDS initial commit"

Write-Host "[5/6] git remote add..." -ForegroundColor Cyan
git remote add origin https://github.com/KODxixi/dds.git

Write-Host "[6/6] git push..." -ForegroundColor Cyan
git push -u origin main

Write-Host ""
Write-Host "Done! https://github.com/KODxixi/dds" -ForegroundColor Green
Read-Host "Press Enter to exit"
