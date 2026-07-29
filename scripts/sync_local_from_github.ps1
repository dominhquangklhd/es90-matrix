$ErrorActionPreference = 'Stop'

$repoPath = Split-Path -Parent $PSScriptRoot
$gitPath = 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe'
$logPath = Join-Path $repoPath '.git\es90-local-sync.log'
$lockPath = Join-Path $repoPath '.git\es90-local-sync.lock'

if (-not (Test-Path -LiteralPath $repoPath) -or -not (Test-Path -LiteralPath $gitPath)) {
  exit 1
}

$lockStream = $null
try {
  $lockStream = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
  exit 0
}

try {
  Set-Location -LiteralPath $repoPath

  $changes = & $gitPath status --porcelain
  if ($changes) {
    Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') skipped: local changes"
    exit 0
  }

  & $gitPath fetch origin main --quiet
  if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }

  $localHead = & $gitPath rev-parse HEAD
  $remoteHead = & $gitPath rev-parse origin/main
  if ($localHead -eq $remoteHead) { exit 0 }

  & $gitPath merge --ff-only origin/main
  if ($LASTEXITCODE -ne 0) { throw 'fast-forward merge failed' }

  Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') synced: $remoteHead"
} catch {
  Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') failed: $($_.Exception.Message)"
  exit 1
} finally {
  if ($lockStream) { $lockStream.Dispose() }
}
