# DDS 每日管道调度脚本
# 由 Windows 任务计划程序调用，每天 02:00 运行
# 检查所有到期管道并执行
# 注意：此脚本为本地临时方案，ECS 就绪后应迁移至云端 cron

$ErrorActionPreference = "Stop"
$DDS_ROOT = "D:\Vault-assets\AI_Projects\DDS"
$LOG_DIR = "$DDS_ROOT\data_out\pipeline_logs"
$PYTHON = (Get-Command python).Source

# 确保日志目录存在
if (-not (Test-Path $LOG_DIR)) {
    New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
}

$TIMESTAMP = Get-Date -Format "yyyyMMdd_HHmmss"
$LOG_FILE = "$LOG_DIR\pipeline_$TIMESTAMP.log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line
}

Write-Log "============================================"
Write-Log "DDS 每日管道调度开始"
Write-Log "============================================"

# ── 1. 检查到期管道 ──
Write-Log "检查到期管道..."
$checkResult = & $PYTHON "$DDS_ROOT\cloud\volcengine\pipeline_scheduler.py" --check 2>&1
Write-Log $checkResult

# ── 2. 运行所有到期管道 ──
# 管道二：土地出让（每周一，status=ready）
$today = Get-Date
if ($today.DayOfWeek -eq 'Monday') {
    Write-Log "=== 周一：运行管道二·土地出让 ==="
    try {
        & $PYTHON "$DDS_ROOT\cloud\volcengine\pipeline_scheduler.py" --run land --execute 2>&1 | ForEach-Object { Write-Log $_ }
    } catch {
        Write-Log "ERROR: 管道二执行失败: $_"
    }
}

# ── 3. 每月 1 日：运行管道三/四（建安成本/开发商信用，status=building）──
if ($today.Day -eq 1) {
    Write-Log "=== 每月第1天：检查管道三/四 ==="
    Write-Log "管道三·建安成本 (status=building, 待建设独立脚本)"
    Write-Log "管道四·开发商信用 (status=building, 待建设独立脚本)"
}

Write-Log "============================================"
Write-Log "DDS 每日管道调度结束"
Write-Log "============================================"