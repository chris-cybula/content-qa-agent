# Runs every free check in one go: services, migrations, Ruff, mypy, web checks, tests and the code-only eval. Never calls Claude.
param(
    [switch]$Quick
)

$root = $PSScriptRoot
$api = Join-Path $root 'api'
$web = Join-Path $root 'web'
$python = Join-Path $api '.venv\Scripts\python.exe'
$ruff = Join-Path $api '.venv\Scripts\ruff.exe'
$mypy = Join-Path $api '.venv\Scripts\mypy.exe'
$testDatabaseUrl = 'postgresql+asyncpg://qa:qa@localhost:5432/content_qa_test'

$missing = @()
foreach ($tool in @($python, $ruff, $mypy)) {
    if (-not (Test-Path $tool)) { $missing += $tool }
}
foreach ($command in @('docker', 'npm.cmd')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { $missing += $command }
}
if ($Quick) { $missing = $missing | Where-Object { $_ -ne 'docker' } }
if ($missing) {
    Write-Host "Missing: $($missing -join ', ')" -ForegroundColor Red
    exit 1
}

function Invoke-PostgresTests {
    $previous = $env:TEST_DATABASE_URL
    $env:TEST_DATABASE_URL = $testDatabaseUrl
    try {
        & $python -m pytest -q
    }
    finally {
        if ($null -eq $previous) { Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue }
        else { $env:TEST_DATABASE_URL = $previous }
    }
}

$steps = @(
    @{ Name = 'Start Postgres and the demo site'; Folder = $root; Full = $true; Run = { docker compose up -d --wait db demo-site } }
    @{ Name = 'Database migrations'; Folder = $api; Full = $true; Run = { & $python -m alembic upgrade head } }
    @{ Name = 'Ruff lint'; Folder = $api; Full = $false; Run = { & $ruff check . } }
    @{ Name = 'Ruff format check'; Folder = $api; Full = $false; Run = { & $ruff format --check . } }
    @{ Name = 'mypy'; Folder = $api; Full = $false; Run = { & $mypy } }
    @{ Name = 'Web checks (ESLint, Stylelint, tsc, Prettier)'; Folder = $web; Full = $false; Run = { npm.cmd run check } }
    @{ Name = 'Tests on SQLite'; Folder = $api; Full = $false; Run = { & $python -m pytest -q } }
    @{ Name = 'Tests on Postgres'; Folder = $api; Full = $true; Run = { Invoke-PostgresTests } }
    @{ Name = 'Code-only eval (100% precision and recall)'; Folder = $api; Full = $true; Run = { & $python -m evals.run_evals --label check --no-save --min-precision 1 --min-recall 1 } }
)

$selected = @($steps | Where-Object { -not ($Quick -and $_.Full) })
$total = [Diagnostics.Stopwatch]::StartNew()
$number = 0

foreach ($step in $selected) {
    $number++
    Write-Host ""
    Write-Host "[$number/$($selected.Count)] $($step.Name)" -ForegroundColor Cyan
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $global:LASTEXITCODE = 0
    Push-Location $step.Folder
    try {
        & $step.Run
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "FAILED: $($step.Name) (exit code $LASTEXITCODE)" -ForegroundColor Red
        Write-Host "$($number - 1) of $($selected.Count) passed before it, $([int]$total.Elapsed.TotalSeconds) s" -ForegroundColor Red
        exit 1
    }
    Write-Host "OK in $([math]::Round($timer.Elapsed.TotalSeconds, 1)) s" -ForegroundColor Green
}

$mode = if ($Quick) { ' (quick: no Docker, Postgres or eval)' } else { '' }
Write-Host ""
Write-Host "All $($selected.Count) of $($selected.Count) checks passed in $([int]$total.Elapsed.TotalSeconds) s$mode" -ForegroundColor Green
