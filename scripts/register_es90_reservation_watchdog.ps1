[CmdletBinding()]
param(
    [string]$TaskName = "ES90 Reservation Hourly Watchdog"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$watchdogPath = Join-Path $PSScriptRoot "invoke_es90_reservation_watchdog.ps1"
if (-not (Test-Path -LiteralPath $watchdogPath)) {
    throw "Watchdog script not found: $watchdogPath"
}

$powershellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$taskArguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $watchdogPath
$actionParameters = @{
    Execute = $powershellPath
    Argument = $taskArguments
}
$action = New-ScheduledTaskAction @actionParameters
$trigger = New-ScheduledTaskTrigger -Daily -At 08:03
$repetitionParameters = @{
    ClassName = "MSFT_TaskRepetitionPattern"
    Namespace = "Root/Microsoft/Windows/TaskScheduler"
    Property = @{
        Interval = "PT5M"
        Duration = "PT10H56M"
        StopAtDurationEnd = $false
    }
    ClientOnly = $true
}
$repetition = New-CimInstance @repetitionParameters
$trigger.CimInstanceProperties["Repetition"].Value = $repetition
$settingsParameters = @{
    StartWhenAvailable = $true
    WakeToRun = $true
    MultipleInstances = "IgnoreNew"
    ExecutionTimeLimit = (New-TimeSpan -Minutes 20)
}
$settings = New-ScheduledTaskSettingsSet @settingsParameters
$principalParameters = @{
    UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    LogonType = "Interactive"
    RunLevel = "Limited"
}
$principal = New-ScheduledTaskPrincipal @principalParameters

$registrationParameters = @{
    TaskName = $TaskName
    Action = $action
    Trigger = $trigger
    Settings = $settings
    Principal = $principal
    Force = $true
}
Register-ScheduledTask @registrationParameters | Out-Null

$registeredTask = Get-ScheduledTask -TaskName $TaskName
if (-not $registeredTask) {
    throw "Unable to read the registered Windows scheduled task."
}
$registeredInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output (
    "Registered: {0}; next run {1}; every 5 minutes from 08:03 to 18:59 daily." -f @(
        $TaskName,
        $registeredInfo.NextRunTime.ToString("yyyy-MM-dd HH:mm:ss")
    )
)
