param([Parameter(ValueFromRemainingArguments = $true)][string[]]$OcrArguments)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$ocrPython = Join-Path $projectRoot '.runtime/ocr/venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $ocrPython)) {
    throw 'OCR environment is missing. See docs/WINDOWS_RUNTIME.md.'
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $ocrPython (Join-Path $PSScriptRoot 'ocr_runtime.py') @OcrArguments
exit $LASTEXITCODE
