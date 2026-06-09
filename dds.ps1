# DDS (Digital Decision System) 本地指挥中心脚本
param (
    [Parameter(Mandatory=$true)]
    [string]$Query  # 接收你的自然语言指令
)

# 1. 配置信息 (请确认与你的云端一致)
$CLOUD_RUN_URL = "https://dds-landchina-scraper-858943527635.us-central1.run.app"

Write-Host "`n[DDS Agent] 正在同步指令: `"$Query`"..." -ForegroundColor Cyan

# 2. 获取 Google Cloud 身份验证令牌
try {
    $token = gcloud auth print-identity-token --quiet
    if (-not $token) { throw "无法获取 Token，请先运行 gcloud auth login" }
} catch {
    Write-Host "[错误] 请确保已安装 gcloud 并完成登录: gcloud auth login" -ForegroundColor Red
    return
}

# 3. 构造请求 Payload (将你的指令封装为 JSON)
$body = @{
    query = $Query
    timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
} | ConvertTo-Json

# 4. 发送指令到云端 DDS
try {
    $headers = @{
        "Authorization" = "Bearer $token"
        "Content-Type"  = "application/json"
    }
    
    $response = Invoke-RestMethod -Uri $CLOUD_RUN_URL -Method Post -Headers $headers -Body $body -ErrorAction Stop
    
    # 5. 输出结果
    Write-Host "`n[DDS 报告]:" -ForegroundColor Green
    Write-Host "--------------------------------------------------"
    Write-Host $response
    Write-Host "--------------------------------------------------`n"
} catch {
    Write-Host "`n[系统报错]: 无法连接到云端引擎。请检查 URL 或云函数日志。" -ForegroundColor Red
    Write-Host $_.Exception.Message
}