$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Updater([string[]] $ForwardedArgs) {
    if ($env:PYTHON) {
        & $env:PYTHON update_feed.py @ForwardedArgs
        exit $LASTEXITCODE
    }

    $candidates = @(
        @{ Command = "py"; Prefix = @("-3.13") },
        @{ Command = "py"; Prefix = @("-3.12") },
        @{ Command = "py"; Prefix = @("-3.11") },
        @{ Command = "python3"; Prefix = @() },
        @{ Command = "python"; Prefix = @() }
    )

    foreach ($candidate in $candidates) {
        if (Get-Command $candidate.Command -ErrorAction SilentlyContinue) {
            & $candidate.Command @($candidate.Prefix) -c `
                "import sys; raise SystemExit(sys.version_info < (3, 11))"
            if ($LASTEXITCODE -eq 0) {
                & $candidate.Command @($candidate.Prefix) update_feed.py @ForwardedArgs
                exit $LASTEXITCODE
            }
        }
    }

    if (Get-Command "uv" -ErrorAction SilentlyContinue) {
        & uv run --python 3.13 --locked update_feed.py @ForwardedArgs
        exit $LASTEXITCODE
    }

    throw "Python 3.11+ is required. Install it, or install uv, then rerun .\update.ps1."
}

Invoke-Updater $args
