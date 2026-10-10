$ErrorActionPreference = 'Stop'
$hexPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $hexPython)) { throw 'Hex Python environment is missing. Run scripts/bootstrap.ps1.' }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $PSScriptRoot + [IO.Path]::PathSeparator + $oldPythonPath
    & $hexPython -m hex_agent @args
    $hexExit = $LASTEXITCODE
} finally { $env:PYTHONPATH = $oldPythonPath }
exit $hexExit
