$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $env:VERIAGENT_HOME) { $env:VERIAGENT_HOME = Join-Path $PSScriptRoot 'runtime' }
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    [Console]::Error.WriteLine('Create .venv and install requirements.txt first. See README.md.')
    exit 1
}
& $projectPython -m veriagent.server
exit $LASTEXITCODE
