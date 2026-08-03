[CmdletBinding()]
param(
    [string]$Repository = "volvo-es90-matrix/es90-matrix",
    [string]$Workflow = "update-es90-reservations.yml",
    [string]$NowIso = "",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ghPath = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path -LiteralPath $ghPath)) {
    $ghCommand = Get-Command gh.exe -ErrorAction Stop
    $ghPath = $ghCommand.Source
}

$seoulTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById("Korea Standard Time")
if ($NowIso) {
    $now = [TimeZoneInfo]::ConvertTime(
        [DateTimeOffset]::Parse($NowIso),
        $seoulTimeZone
    )
} else {
    $now = [TimeZoneInfo]::ConvertTime(
        [DateTimeOffset]::UtcNow,
        $seoulTimeZone
    )
}

$logDirectory = Join-Path $env:LOCALAPPDATA "ES90Matrix"
$logPath = Join-Path $logDirectory "reservation-watchdog.log"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Write-WatchdogLog {
    param([string]$Message)
    $line = "{0} {1}" -f $now.ToString("yyyy-MM-dd HH:mm:ss zzz"), $Message
    Write-Output $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Invoke-GhJson {
    param([string[]]$Arguments)
    $output = & $ghPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI failed: gh $($Arguments -join ' ')"
    }
    return (($output -join "`n") | ConvertFrom-Json)
}

function Get-RepositoryVersion {
    $encodedLines = & $ghPath @(
        "api",
        "repos/$Repository/contents/version.json?ref=main",
        "--jq",
        ".content"
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read version.json from GitHub."
    }
    $encoded = ($encodedLines -join "").Trim()
    $json = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String($encoded)
    )
    return ($json | ConvertFrom-Json)
}

try {
    if ($now.Hour -lt 8 -or $now.Hour -gt 18) {
        Write-WatchdogLog "Outside the required window (08:00-18:59); exiting."
        exit 0
    }

    $targetHour = [Math]::Min($now.Hour, 18)
    $targetSlot = [DateTimeOffset]::new(
        $now.Year,
        $now.Month,
        $now.Day,
        $targetHour,
        0,
        0,
        $now.Offset
    )
    $version = Get-RepositoryVersion
    $observedAt = [DateTimeOffset]::Parse(
        [string]$version.reservationUpdatedAt
    )

    if ($observedAt -ge $targetSlot) {
        Write-WatchdogLog (
            "Healthy: target {0}, observed {1}" -f @(
                $targetSlot.ToString("HH:mm"),
                $observedAt.ToString("HH:mm:ss")
            )
        )
        exit 0
    }

    $runs = Invoke-GhJson -Arguments @(
        "run", "list",
        "--repo", $Repository,
        "--workflow", $Workflow,
        "--limit", "10",
        "--json", "databaseId,status,createdAt,url"
    )
    $activeRun = $runs | Where-Object {
        $_.status -in @("queued", "in_progress", "waiting", "pending", "requested")
    } | Select-Object -First 1

    if ($activeRun) {
        Write-WatchdogLog "An update is already active; skipping duplicate dispatch: $($activeRun.url)"
        exit 0
    }

    if ($DryRun) {
        Write-WatchdogLog (
            "DRY RUN: observed {1} is older than target {0}; dispatch required." -f @(
                $targetSlot.ToString("HH:mm"),
                $observedAt.ToString("yyyy-MM-dd HH:mm:ss")
            )
        )
        exit 0
    }

    $dispatchStartedAt = [DateTimeOffset]::UtcNow
    & $ghPath workflow run $Workflow --repo $Repository --ref main
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to dispatch the reservation update workflow."
    }
    Start-Sleep -Seconds 4

    $dispatchedRuns = Invoke-GhJson -Arguments @(
        "run", "list",
        "--repo", $Repository,
        "--workflow", $Workflow,
        "--event", "workflow_dispatch",
        "--limit", "10",
        "--json", "databaseId,status,createdAt,url"
    )
    $run = $dispatchedRuns | Where-Object {
        [DateTimeOffset]::Parse([string]$_.createdAt) -ge $dispatchStartedAt.AddMinutes(-1)
    } | Sort-Object { [DateTimeOffset]::Parse([string]$_.createdAt) } -Descending |
        Select-Object -First 1

    if (-not $run) {
        throw "Unable to find the dispatched GitHub Actions run."
    }

    Write-WatchdogLog "Recovery run started: $($run.url)"
    & $ghPath run watch ([string]$run.databaseId) --repo $Repository --exit-status
    if ($LASTEXITCODE -ne 0) {
        throw "Recovery run failed: $($run.url)"
    }

    $verifiedVersion = Get-RepositoryVersion
    $verifiedAt = [DateTimeOffset]::Parse(
        [string]$verifiedVersion.reservationUpdatedAt
    )
    if ($verifiedAt -lt $targetSlot) {
        throw (
            "Reservation time is still older than the target after a successful run: " +
            "$($verifiedAt.ToString('o')) < $($targetSlot.ToString('o'))"
        )
    }

    Write-WatchdogLog "Recovery complete: reservationUpdatedAt=$($verifiedAt.ToString('o'))"
    exit 0
} catch {
    Write-WatchdogLog "Error: $($_.Exception.Message)"
    exit 1
}
