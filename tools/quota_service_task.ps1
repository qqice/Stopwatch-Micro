[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'status', 'start', 'stop')]
    [string]$Action = 'status',

    [string]$PythonwPath,
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\.artifacts\lan-config.json')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$taskName = 'Stopwatch Micro LAN Quota Service'
$servicePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'quota_service.py')).Path

function Resolve-Pythonw {
    if ($PythonwPath) {
        return (Resolve-Path -LiteralPath $PythonwPath).Path
    }
    $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) {
        throw 'pythonw.exe was not found. Install Python or pass -PythonwPath explicitly.'
    }
    return $command.Source
}

function Show-QuotaServiceTask {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        [pscustomobject]@{ TaskName = $taskName; Installed = $false; State = 'Not installed' }
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    [pscustomobject]@{
        TaskName = $taskName
        Installed = $true
        State = $task.State
        LastRunTime = $info.LastRunTime
        LastTaskResult = ('0x{0:X}' -f $info.LastTaskResult)
    }
}

switch ($Action) {
    'install' {
        $pythonw = Resolve-Pythonw
        $resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $arguments = '-u "{0}" --config "{1}"' -f $servicePath, $resolvedConfig
        $actionDefinition = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $PSScriptRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
        $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
            -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
        $definition = New-ScheduledTask -Action $actionDefinition -Trigger $trigger -Principal $principal `
            -Settings $settings `
            -Description 'Serves the latest Stopwatch Micro quota snapshot to trusted LAN devices.'
        Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Show-QuotaServiceTask
    }
    'start' {
        Start-ScheduledTask -TaskName $taskName
        Show-QuotaServiceTask
    }
    'stop' {
        Stop-ScheduledTask -TaskName $taskName
        Show-QuotaServiceTask
    }
    'status' {
        Show-QuotaServiceTask
    }
}
