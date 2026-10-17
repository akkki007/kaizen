# One-command demo reset on Windows (docs/demo.md): clean one repository's incident state,
# migrate, seed the resolved demo history.
#
#   .\scripts\demo-reset.ps1 [-Repo owner/name] [-Yes] [-DropGraph]
#
# Never production: refuses CD_ENV=prod (environment or .env, checked again in demo_reset.py).
param(
    [string]$Repo = "yashchandnani07/shoplite",
    [switch]$Yes,
    [switch]$DropGraph
)
$ErrorActionPreference = "Stop"

if ($env:CD_ENV -eq "prod") {
    Write-Error "refusing: CD_ENV=prod. demo-reset never runs against production."
    exit 2
}

$root = Split-Path -Parent $PSScriptRoot
$uv = @("python", "-m", "uv")
if (Get-Command uv -ErrorAction SilentlyContinue) { $uv = @("uv") }
$extra = @()
if ($Yes) { $extra += "--yes" }
if ($DropGraph) { $extra += "--drop-graph" }

function Invoke-Step([string]$name, [string[]]$cmd) {
    Write-Host "== $name"
    $exe = $cmd[0]
    $rest = @()
    if ($cmd.Length -gt 1) { $rest = $cmd[1..($cmd.Length - 1)] }
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Push-Location (Join-Path $root "core")
try {
    Invoke-Step "migrate" ($uv + @("run", "alembic", "upgrade", "head"))
    Invoke-Step "clean $Repo" ($uv + @("run", "python", "../scripts/demo_reset.py", $Repo) + $extra)
    Invoke-Step "seed demo history" ($uv + @("run", "python", "-m", "causaldeploy.cli", "demo-history", $Repo))
    Write-Host "demo state ready for $Repo"
}
finally {
    Pop-Location
}
