[CmdletBinding()]
param(
    [int]$Port = 8010,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repositoryRoot

if ($Install) {
    python -m pip install -r requirements.txt
}

python -m uvicorn server:app --reload --host 127.0.0.1 --port $Port
