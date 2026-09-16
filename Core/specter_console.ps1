# SPECTER SOVEREIGN CONSOLE - POWERSHELL INTERFACE
# ===================================================
$Host.UI.RawUI.WindowTitle = "SPECTER SOVEREIGN CONSOLE - PowerShell Hub v5.1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8

$PY = "python"
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $PY = "python"
} elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
    $PY = "py"
}

$Script = Join-Path $PSScriptRoot "specter_terminal.py"
if (-not (Test-Path $Script)) {
    $Script = Join-Path $PSScriptRoot "Core\specter_terminal.py"
}

& $PY $Script
