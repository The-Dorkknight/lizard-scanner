# find_cr_scan_calibration.ps1 -- look for Creality CR Scan / JM Studio calibration files on a Windows PC
# https://github.com/The-Dorkknight/lizard-scanner
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
# WORK IN PROGRESS, UNTESTED: written without a Windows machine to run it on.
#
# READ-ONLY on everything it searches. It only COPIES what it finds into a new folder on your Desktop
# (CRScanCalibHunt_<date>), with a report, so you can bring that folder back to Linux.
# Start it with Find-CR-Scan-Calibration.bat (double-click), or:
#   powershell -ExecutionPolicy Bypass -File find_cr_scan_calibration.ps1 [-Serial CRSL1234567]

param([string]$Serial = "")

$ErrorActionPreference = "SilentlyContinue"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$out = Join-Path ([Environment]::GetFolderPath("Desktop")) "CRScanCalibHunt_$stamp"
New-Item -ItemType Directory -Path $out | Out-Null
$report = Join-Path $out "report.txt"
function Say([string]$text) { Write-Host $text; Add-Content -Path $report -Value $text }

if (-not $Serial) {
    $Serial = Read-Host "Scanner serial (starts CRSL; shown in Lizard Scanner's badge). Press Enter to skip"
}
Say "CR Scan calibration hunt, $(Get-Date -Format 'yyyy-MM-dd HH:mm'), serial: '$Serial'"
Say "Copies go to: $out"
Say ""

# 1. Where the apps live: their install folders and their per-user / shared data folders
$roots = @($env:APPDATA, $env:LOCALAPPDATA, (Join-Path $env:LOCALAPPDATA "Low"), $env:ProgramData,
           ${env:ProgramFiles}, ${env:ProgramFiles(x86)}, (Join-Path $env:USERPROFILE "Documents"))
$appPattern = '(?i)creality|cr[ _-]?scan|crscan|jm[ _-]?studio|jmstudio|lizard|revopoint|3d ?scan'
$appDirs = @()
foreach ($r in $roots) {
    if ($r -and (Test-Path $r)) {
        $appDirs += Get-ChildItem -Path $r -Directory -Depth 1 | Where-Object { $_.FullName -match $appPattern }
    }
}
$appDirs = $appDirs | Sort-Object FullName -Unique
Say "== App folders found =="
if (-not $appDirs) { Say "  none: is CR Scan installed on this PC?" }
foreach ($d in $appDirs) { Say "  $($d.FullName)" }
Say ""

# 2. Files that look like calibration, by name or type
$namePattern = '(?i)calib|intrinsic|extrinsic|param|camera|device|sensor|lut|' + [regex]::Escape($Serial)
$typePattern = '(?i)\.(yml|yaml|xml|json|bin|dat|cfg|ini|txt|calib|param|db|sqlite|npz|npy)$'
$hits = @()
foreach ($d in $appDirs) {
    $hits += Get-ChildItem -Path $d.FullName -File -Recurse |
        Where-Object { ($_.Name -match $namePattern -or ($Serial -and $_.FullName -match [regex]::Escape($Serial))) -and
                       $_.Name -match $typePattern -and $_.Length -lt 50MB }
}

# 3. Small files whose CONTENT mentions the serial (calibration is usually filed under it)
if ($Serial) {
    foreach ($d in $appDirs) {
        Get-ChildItem -Path $d.FullName -File -Recurse | Where-Object { $_.Length -lt 20MB } | ForEach-Object {
            if (Select-String -Path $_.FullName -Pattern $Serial -SimpleMatch -Quiet) { $hits += $_ }
        }
    }
}
$hits = $hits | Sort-Object FullName -Unique
Say "== Files that look like calibration ($($hits.Count)) =="
$i = 0
foreach ($f in $hits) {
    $i++
    Say ("  {0,10:N0} bytes  {1:yyyy-MM-dd HH:mm}  {2}" -f $f.Length, $f.LastWriteTime, $f.FullName)
    $dest = Join-Path $out ("found\{0:D3}_{1}" -f $i, $f.Name)
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Copy-Item -Path $f.FullName -Destination $dest
}
Say ""

# 4. What the apps wrote most recently (a downloaded calibration is cached on first connect)
Say "== Newest 40 files in the app folders =="
$recent = foreach ($d in $appDirs) { Get-ChildItem -Path $d.FullName -File -Recurse }
$recent | Sort-Object LastWriteTime -Descending | Select-Object -First 40 | ForEach-Object {
    Say ("  {0:yyyy-MM-dd HH:mm}  {1,10:N0} bytes  {2}" -f $_.LastWriteTime, $_.Length, $_.FullName)
}
Say ""

# 5. Web addresses and calibration words inside the programs (where would it download from?)
Say "== Web addresses mentioned inside the apps' program files (first 60) =="
$urls = @()
foreach ($d in $appDirs) {
    Get-ChildItem -Path $d.FullName -File -Recurse -Include *.exe, *.dll, *.json, *.ini, *.config, *.xml |
        Where-Object { $_.Length -lt 200MB } | ForEach-Object {
            $urls += Select-String -Path $_.FullName -Pattern 'https?://[A-Za-z0-9./_%?=&-]{6,120}' -AllMatches |
                ForEach-Object { $_.Matches.Value }
        }
}
$urls | Sort-Object -Unique | Select-Object -First 60 | ForEach-Object { Say "  $_" }
Say ""
Say "Done. Bring the folder $out back to Linux (it has report.txt and copies of what was found)."
Say "Nothing outside that folder was changed."
Write-Host ""
Read-Host "Press Enter to close"
