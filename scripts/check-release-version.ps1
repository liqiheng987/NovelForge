param(
    [Parameter(Mandatory = $true)]
    [string]$Tag
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$version = $Tag.Trim()
if ($version.StartsWith("v")) {
    $version = $version.Substring(1)
}
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Release tag must use semantic versioning, for example v0.1.0. Received: $Tag"
}

$repo = Split-Path -Parent $PSScriptRoot
$rootPackage = Get-Content -Raw (Join-Path $repo "package.json") | ConvertFrom-Json
$webPackage = Get-Content -Raw (Join-Path $repo "web-frontend\package.json") | ConvertFrom-Json
$tauriConfig = Get-Content -Raw (Join-Path $repo "src-tauri\tauri.conf.json") | ConvertFrom-Json
$cargoText = Get-Content -Raw (Join-Path $repo "src-tauri\Cargo.toml")
$cargoVersion = [regex]::Match($cargoText, '(?ms)^\[package\].*?^version\s*=\s*"([^"]+)"').Groups[1].Value

$versions = [ordered]@{
    "package.json" = [string]$rootPackage.version
    "web-frontend/package.json" = [string]$webPackage.version
    "src-tauri/Cargo.toml" = $cargoVersion
    "src-tauri/tauri.conf.json" = [string]$tauriConfig.version
}

$mismatches = $versions.GetEnumerator() | Where-Object { $_.Value -ne $version }
if ($mismatches) {
    $details = ($mismatches | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ", "
    throw "Release version $version does not match: $details"
}

Write-Output "Release version verified: $version"
