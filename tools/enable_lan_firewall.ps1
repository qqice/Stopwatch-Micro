# Run from an elevated PowerShell. Opens only the quota service, on the LAN.
#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$InterfaceAlias = 'WLAN', [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid port' }
if (-not (Get-NetFirewallRule -Name StopwatchMicroLAN -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name StopwatchMicroLAN -DisplayName 'Stopwatch Micro LAN quota (local subnet only)' `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -RemoteAddress LocalSubnet -InterfaceAlias $InterfaceAlias -Profile Any | Out-Null
}
Get-NetFirewallRule -Name StopwatchMicroLAN | Select-Object Name, Enabled, Direction, Action
