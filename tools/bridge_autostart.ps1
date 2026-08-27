[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'remove', 'status', 'start', 'stop')]
    [string]$Action = 'status',

    [string]$PythonwPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$taskName = 'Stopwatch Micro Wireless Bridge'
$bridgePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'stopwatch_bridge.py')).Path

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

function Show-BridgeTask {
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
        $python = Join-Path (Split-Path -Parent $pythonw) 'python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw "python.exe was not found beside pythonw.exe: $python"
        }
        & $python -c 'import hid'
        if ($LASTEXITCODE -ne 0) {
            throw "hidapi is missing from $python. Install it with: python -m pip install hidapi"
        }

        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $arguments = '-u "{0}" --transport bluetooth' -f $bridgePath
        $actionDefinition = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $PSScriptRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
        $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
            -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
        $definition = New-ScheduledTask -Action $actionDefinition -Trigger $trigger -Principal $principal `
            -Settings $settings `
            -Description 'Keeps Stopwatch Micro Codex quota and reset time updated over paired Bluetooth HID.'
        Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 1
        Show-BridgeTask
    }
    'remove' {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
        Show-BridgeTask
    }
    'start' {
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 1
        Show-BridgeTask
    }
    'stop' {
        Stop-ScheduledTask -TaskName $taskName
        Show-BridgeTask
    }
    'status' {
        Show-BridgeTask
    }
}
