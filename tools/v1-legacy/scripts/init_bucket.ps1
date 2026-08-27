$tmp = "$env:TEMP\keep.txt"
[System.IO.File]::WriteAllText($tmp, "")
$b   = "gs://dds-data-lake"
$log = "D:\Vault-assets\AI_Projects\DDS\data_out\init_bucket.log"
New-Item -Force -ItemType Directory "D:\Vault-assets\AI_Projects\DDS\data_out" | Out-Null
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log

$paths = @(
    "real_estate_data/landchina/normalized/.keep",
    "real_estate_data/landchina/logs/.keep",
    "real_estate_data/provincial/hangzhou/raw/.keep",
    "real_estate_data/provincial/hangzhou/normalized/.keep",
    "real_estate_data/provincial/sanya/raw/.keep",
    "real_estate_data/provincial/sanya/normalized/.keep",
    "real_estate_data/client_provided/.keep",
    "real_estate_data/social_sentiment/.keep",
    "real_estate_data/gis/.keep",
    "shared/manifests/.keep",
    "shared/schemas/.keep"
)

$i = 2
foreach ($p in $paths) {
    gcloud storage cp $tmp "$b/$p" 2>&1 | Out-Null
    "$i/12 $(if($LASTEXITCODE -eq 0){'ok'}else{'FAIL'}) $p  $(Get-Date -Format 'HH:mm:ss')" | Tee-Object $log -Append
    $i++
}

# CATALOG.md
@"
# DDS Data Lake
bucket : dds-data-lake
region : us (multi-region)
created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

## 结构
real_estate_data/
  landchina/raw|normalized|logs
  provincial/hangzhou/raw|normalized
  provincial/sanya/raw|normalized
  client_provided/
  social_sentiment/
  gis/
shared/manifests|schemas
"@ | Out-File "$env:TEMP\CATALOG.md" -Encoding utf8
gcloud storage cp "$env:TEMP\CATALOG.md" "$b/CATALOG.md" 2>&1 | Out-Null
"12/12 $(if($LASTEXITCODE -eq 0){'ok'}else{'FAIL'}) CATALOG.md  $(Get-Date -Format 'HH:mm:ss')" | Tee-Object $log -Append

"DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append
