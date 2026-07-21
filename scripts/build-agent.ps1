$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$agent = Join-Path $repo "python-agent"
$venv = Join-Path $agent ".build-venv"
$python = Join-Path $venv "Scripts\python.exe"

if (!(Test-Path -LiteralPath $python)) {
    python -m venv $venv
}

& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $agent "requirements.txt") -r (Join-Path $agent "requirements-build.txt")

Push-Location $agent
try {
    & $python -m PyInstaller --clean --noconfirm --onedir --name novelforge-agent --paths $agent --distpath (Join-Path $agent "dist") --workpath (Join-Path $agent "build") --specpath (Join-Path $agent "build") (Join-Path $agent "app.py")
}
finally {
    Pop-Location
}

Write-Output "Agent sidecar built at $agent\dist\novelforge-agent"
