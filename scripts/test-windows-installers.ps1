param(
    [Parameter(Mandatory = $true)]
    [string]$CurrentMsi,

    [Parameter(Mandatory = $true)]
    [string]$CurrentNsis,

    [Parameter(Mandatory = $true)]
    [string]$BaselineMsi,

    [Parameter(Mandatory = $true)]
    [string]$CurrentVersion,

    [Parameter(Mandatory = $true)]
    [string]$BaselineVersion,

    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-FileExists([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required installer was not found: $Path"
    }
}

function Assert-Signed([string]$Path) {
    if (!$RequireSignature) { return }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne "Valid") {
        throw "Authenticode signature is not valid for $Path. Status: $($signature.Status)"
    }
}

function Stop-NovelForgeProcesses {
    Get-Process -Name "novelforge", "novelforge-agent" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

function Invoke-Msi([string]$Operation, [string]$Path, [string]$LogName) {
    $logPath = Join-Path $env:RUNNER_TEMP $LogName
    $arguments = @($Operation, "`"$Path`"", "/qn", "/norestart", "/L*v", "`"$logPath`"")
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -notin 0, 3010) {
        if (Test-Path -LiteralPath $logPath) { Get-Content -Tail 120 -LiteralPath $logPath }
        throw "msiexec $Operation failed with exit code $($process.ExitCode)"
    }
}

function Get-InstalledEntry {
    $roots = @(
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($root in $roots) {
        if (!(Test-Path $root)) { continue }
        $entry = Get-ChildItem $root -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue } |
            Where-Object { $_.PSObject.Properties["DisplayName"] -and $_.DisplayName -eq "NovelForge" } |
            Select-Object -First 1
        if ($entry) { return $entry }
    }
    return $null
}

function Get-InstalledExecutable {
    $entry = Get-InstalledEntry
    $candidates = @()
    if ($entry -and $entry.PSObject.Properties["InstallLocation"] -and $entry.InstallLocation) {
        $candidates += Join-Path ([string]$entry.InstallLocation).Trim('"') "novelforge.exe"
    }
    $registryPath = "HKCU:\Software\novelforge\NovelForge"
    if (Test-Path $registryPath) {
        $registry = Get-ItemProperty $registryPath
        if ($registry.PSObject.Properties["InstallDir"] -and $registry.InstallDir) {
            $candidates += Join-Path ([string]$registry.InstallDir) "novelforge.exe"
        }
        if ($registry.PSObject.Properties["(default)"] -and $registry.'(default)') {
            $candidates += Join-Path ([string]$registry.'(default)') "novelforge.exe"
        }
    }
    $candidates += Join-Path $env:ProgramFiles "NovelForge\novelforge.exe"
    $candidates += Join-Path $env:LOCALAPPDATA "NovelForge\novelforge.exe"
    return $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}

function Assert-InstalledVersion([string]$Expected) {
    $entry = Get-InstalledEntry
    if (!$entry) { throw "NovelForge is not registered as an installed application" }
    if (!$entry.PSObject.Properties["DisplayVersion"]) {
        throw "NovelForge uninstall registration has no DisplayVersion"
    }
    if ([string]$entry.DisplayVersion -ne $Expected) {
        throw "Expected installed version $Expected, found $($entry.DisplayVersion)"
    }
}

function Test-AppStartup([string]$Label, [bool]$ValidateSignature = $true) {
    $executable = Get-InstalledExecutable
    if (!$executable) { throw "NovelForge executable was not found after $Label" }
    if ($ValidateSignature) { Assert-Signed $executable }

    $database = Join-Path $env:APPDATA "NovelForge\storage\novel_forge.db"
    $process = Start-Process -FilePath $executable -PassThru
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) {
            $logs = Join-Path $env:APPDATA "NovelForge\logs"
            if (Test-Path $logs) { Get-ChildItem $logs -File | ForEach-Object { Get-Content -Tail 80 $_.FullName } }
            throw "NovelForge exited during $Label with code $($process.ExitCode)"
        }
        if (Test-Path -LiteralPath $database -PathType Leaf) { break }
        Start-Sleep -Seconds 2
    }
    if (!(Test-Path -LiteralPath $database -PathType Leaf)) {
        throw "NovelForge did not initialize its data store during $Label"
    }
    Stop-NovelForgeProcesses
}

function Assert-Uninstalled {
    $deadline = (Get-Date).AddSeconds(45)
    do {
        if (!(Get-InstalledExecutable) -and !(Get-InstalledEntry)) { return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "NovelForge files or uninstall registration remain after uninstall"
}

Assert-FileExists $CurrentMsi
Assert-FileExists $CurrentNsis
Assert-FileExists $BaselineMsi
Assert-Signed $CurrentMsi
Assert-Signed $CurrentNsis

Stop-NovelForgeProcesses

# Upgrade acceptance: install a previous version, initialize user data, then upgrade in place.
Invoke-Msi "/i" $BaselineMsi "baseline-install.log"
Assert-InstalledVersion $BaselineVersion
Test-AppStartup "baseline startup" $false
$storage = Join-Path $env:APPDATA "NovelForge\storage"
$marker = Join-Path $storage "upgrade-preservation-marker.txt"
Set-Content -LiteralPath $marker -Value "preserve-across-upgrade" -Encoding utf8
Invoke-Msi "/i" $CurrentMsi "upgrade-install.log"
Assert-InstalledVersion $CurrentVersion
if (!(Test-Path -LiteralPath $marker -PathType Leaf)) { throw "User data was removed during upgrade" }
Test-AppStartup "post-upgrade startup"
Invoke-Msi "/x" $CurrentMsi "upgrade-uninstall.log"
Assert-Uninstalled
if (!(Test-Path -LiteralPath $marker -PathType Leaf)) { throw "User data was removed during uninstall" }

# Fresh MSI acceptance.
Invoke-Msi "/i" $CurrentMsi "fresh-msi-install.log"
Assert-InstalledVersion $CurrentVersion
Test-AppStartup "fresh MSI startup"
Invoke-Msi "/x" $CurrentMsi "fresh-msi-uninstall.log"
Assert-Uninstalled

# Fresh NSIS acceptance.
$nsisInstall = Start-Process -FilePath $CurrentNsis -ArgumentList "/S" -Wait -PassThru
if ($nsisInstall.ExitCode -ne 0) { throw "NSIS install failed with exit code $($nsisInstall.ExitCode)" }
Assert-InstalledVersion $CurrentVersion
Test-AppStartup "fresh NSIS startup"
$nsisExecutable = Get-InstalledExecutable
if (!$nsisExecutable) { throw "NSIS installation directory was not found" }
$uninstaller = Join-Path (Split-Path -Parent $nsisExecutable) "uninstall.exe"
Assert-FileExists $uninstaller
$nsisUninstall = Start-Process -FilePath $uninstaller -ArgumentList "/S" -Wait -PassThru
if ($nsisUninstall.ExitCode -ne 0) { throw "NSIS uninstall failed with exit code $($nsisUninstall.ExitCode)" }
Assert-Uninstalled

Write-Output "Windows installer acceptance passed: upgrade, MSI, NSIS, startup, data preservation, and uninstall."
