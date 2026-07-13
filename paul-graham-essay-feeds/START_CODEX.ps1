$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    Write-Error "Codex CLI was not found. Open CODEX_KICKOFF_PROMPT.md and paste it into Codex from this repository root."
    exit 127
}

$prompt = Get-Content -Raw -Path "CODEX_KICKOFF_PROMPT.md"
& codex $prompt
exit $LASTEXITCODE
