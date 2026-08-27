$tmp = "$env:TEMP\keep.txt"; [System.IO.File]::WriteAllText($tmp, "")
$b = "gs://dds-data-lake"
$log = "D:\Vault-assets\AI_Projects\DDS\data_out\upload_dirs.log"
"START $(Get-Date)" | Out-File $log

$paths = @(
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

foreach ($p in $paths) {
    $r = gcloud storage cp $tmp "$b/$p" 2>&1
    $status = if ($LASTEXITCODE -eq 0) { "ok" } else { "fail: $r" }
    "$status $p" | Tee-Object -FilePath $log -Append
}

# CATALOG.md
$catalog = "# DDS Data Lake`nbucket: dds-data-lake`nregion: asia-east1`ncreated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n"
$catalog | Out-File "$env:TEMP\CATALOG.md" -Encoding utf8
$r = gcloud storage cp "$env:TEMP\CATALOG.md" "$b/CATALOG.md" 2>&1
"$(if($LASTEXITCODE -eq 0){'ok'}else{'fail'}) CATALOG.md" | Tee-Object -FilePath $log -Append

"DONE $(Get-Date)" | Out-File $log -Append
