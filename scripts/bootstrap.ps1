# Run from any directory. This installs only into the project's .venv.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = "1"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $HexLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    $HexLauncherArguments = @("-3")
    if (-not $HexLauncher) {
        $HexLauncher = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
        $HexLauncherArguments = @()
    }
    if (-not $HexLauncher) { throw "Install Python 3.11 or newer, then run this script again." }
    & $HexLauncher.Source @HexLauncherArguments -c "import sys; assert sys.version_info >= (3,11), 'Python >=3.11 required'"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is required." }
    & $HexLauncher.Source @HexLauncherArguments -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the project Python environment." }
}
$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -c "import sys; assert sys.version_info >= (3,11), 'Python >=3.11 required'"
if ($LASTEXITCODE -ne 0) { throw "The project environment requires Python 3.11 or newer." }
& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed; do not continue." }
& $Python -m hex_agent doctor
if ($LASTEXITCODE -ne 0) { throw "Doctor failed." }
& $Python -m hex_agent validate
if ($LASTEXITCODE -ne 0) { throw "Configuration validation failed." }
Write-Host "HEX installed. Local runtime and configuration checks passed."
Write-Host "Next: import the HEX-Skill zip from Releases into your host (e.g. Codex) and ask it to use HEX; run commands: docs/RUNTIME.md."
