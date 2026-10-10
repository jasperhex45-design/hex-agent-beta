$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = "1"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Run scripts\bootstrap.ps1 first." }
$Name = "demo-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [Guid]::NewGuid().ToString("N").Substring(0,6)
$Out = Join-Path "outputs" $Name
& $Python -m hex_agent demo --out $Out
if ($LASTEXITCODE -ne 0) { throw "Demo failed." }
Start-Process (Join-Path $Root "$Out\result.html")
