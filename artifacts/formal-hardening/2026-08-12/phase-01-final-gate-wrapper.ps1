$ErrorActionPreference = "Stop"

$workspace = [System.IO.Path]::GetFullPath("D:\Vibe C\客服\wechat--main")
$artifactRoot = Join-Path $workspace "artifacts\formal-hardening\2026-08-12"
$formalRoot = [System.IO.Path]::GetFullPath(
    "C:\Users\14913\AppData\Roaming\WeChatStyleReplyAssistant"
)
$beforePath = Join-Path $artifactRoot "phase-01-final-gate-before.json"
$afterPath = Join-Path $artifactRoot "phase-01-final-gate-after.json"
$currentPath = Join-Path $artifactRoot "phase-01-final-gate-current.json"
$junitPath = Join-Path $artifactRoot "phase-01-final-gate.xml"
$compilePath = Join-Path $artifactRoot "phase-01-final-gate-compileall.txt"

foreach ($target in @($beforePath, $afterPath, $currentPath, $junitPath, $compilePath)) {
    if (Test-Path -LiteralPath $target) {
        throw "Append-only gate artifact already exists: $target"
    }
}

$gateRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("wechat-assistant-formal-gate-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $gateRoot | Out-Null
$env:WECHAT_ASSISTANT_DATA_DIR = $gateRoot

function Get-Sha256Text([string] $value) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($value)
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace("-", "").ToLower()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-FormalSnapshot {
    $files = @(
        Get-ChildItem -LiteralPath $formalRoot -Recurse -File |
            Sort-Object FullName
    )
    $entries = @(
        foreach ($file in $files) {
            [ordered]@{
                relative_path = $file.FullName.Substring($formalRoot.Length).TrimStart("\").Replace("\", "/")
                size = $file.Length
                sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLower()
                mtime_utc = $file.LastWriteTimeUtc.ToString("o")
            }
        }
    )
    $rows = @(
        $entries | ForEach-Object {
            "$($_.relative_path)|$($_.size)|$($_.sha256)|$($_.mtime_utc)"
        }
    )
    return [ordered]@{
        root = $formalRoot
        captured_utc = [DateTime]::UtcNow.ToString("o")
        file_count = $files.Count
        tree_sha256 = Get-Sha256Text ($rows -join "`n")
        files = $entries
    }
}

function Get-MismatchCount($reference, $difference) {
    $referenceRows = @(
        $reference.files | ForEach-Object {
            "$($_.relative_path)|$($_.size)|$($_.sha256)|$($_.mtime_utc)"
        }
    )
    $differenceRows = @(
        $difference.files | ForEach-Object {
            "$($_.relative_path)|$($_.size)|$($_.sha256)|$($_.mtime_utc)"
        }
    )
    return @(Compare-Object -ReferenceObject $referenceRows -DifferenceObject $differenceRows).Count
}

Write-Output "GATE_TEMP_ROOT=$gateRoot"
Write-Output "WECHAT_ASSISTANT_DATA_DIR=$env:WECHAT_ASSISTANT_DATA_DIR"
Write-Output "TEMP_ROOT_PRESERVED=true"

$before = Get-FormalSnapshot
$before | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $beforePath -Encoding UTF8
Write-Output "BEFORE_COUNT=$($before.file_count)"
Write-Output "BEFORE_TREE_SHA256=$($before.tree_sha256)"
Write-Output "BEFORE_MANIFEST_SHA256=$((Get-FileHash -LiteralPath $beforePath -Algorithm SHA256).Hash.ToLower())"

& .\.venv\Scripts\python.exe -m pytest tests -v --junitxml=$junitPath
$pytestExit = $LASTEXITCODE
Write-Output "PYTEST_EXIT=$pytestExit"

$after = Get-FormalSnapshot
$after | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $afterPath -Encoding UTF8
$afterMismatch = Get-MismatchCount $before $after
Write-Output "AFTER_COUNT=$($after.file_count)"
Write-Output "AFTER_TREE_SHA256=$($after.tree_sha256)"
Write-Output "AFTER_MANIFEST_SHA256=$((Get-FileHash -LiteralPath $afterPath -Algorithm SHA256).Hash.ToLower())"
Write-Output "AFTER_MISMATCH=$afterMismatch"
if ($afterMismatch -ne 0) {
    Write-Output "GATE_STOP_REASON=formal_appdata_changed_after_pytest"
    exit 91
}

$compileOutput = @(
    & .\.venv\Scripts\python.exe -m compileall -q -f -x "[\\/]artifacts[\\/]" . 2>&1
)
$compileExit = $LASTEXITCODE
@($compileOutput; "COMPILEALL_EXIT=$compileExit") |
    Set-Content -LiteralPath $compilePath -Encoding UTF8
$compileOutput | ForEach-Object { Write-Output $_ }
Write-Output "COMPILEALL_EXIT=$compileExit"

$current = Get-FormalSnapshot
$current | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $currentPath -Encoding UTF8
$currentMismatch = Get-MismatchCount $before $current
Write-Output "CURRENT_COUNT=$($current.file_count)"
Write-Output "CURRENT_TREE_SHA256=$($current.tree_sha256)"
Write-Output "CURRENT_MANIFEST_SHA256=$((Get-FileHash -LiteralPath $currentPath -Algorithm SHA256).Hash.ToLower())"
Write-Output "CURRENT_MISMATCH=$currentMismatch"
if ($currentMismatch -ne 0) {
    Write-Output "GATE_STOP_REASON=formal_appdata_changed_after_compileall"
    exit 92
}

Write-Output "JUNIT_SHA256=$((Get-FileHash -LiteralPath $junitPath -Algorithm SHA256).Hash.ToLower())"
Write-Output "COMPILE_ARTIFACT_SHA256=$((Get-FileHash -LiteralPath $compilePath -Algorithm SHA256).Hash.ToLower())"
Write-Output "GATE_COMPLETED=true"

if ($pytestExit -ne 0) {
    exit $pytestExit
}
if ($compileExit -ne 0) {
    exit $compileExit
}
exit 0
