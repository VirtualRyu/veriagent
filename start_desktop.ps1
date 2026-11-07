$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not $env:VERIAGENT_HOME) { $env:VERIAGENT_HOME = Join-Path $PSScriptRoot 'runtime' }
& $projectPython -m veriagent.desktop
exit $LASTEXITCODE
