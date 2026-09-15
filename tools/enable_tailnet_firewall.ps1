#Requires -RunAsAdministrator
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$DeviceAddress, [int]$Port=8765)
$ErrorActionPreference='Stop'
$ip=[System.Net.IPAddress]::Parse($DeviceAddress)
$bytes=$ip.GetAddressBytes()
if ($bytes.Length -ne 4 -or $bytes[0] -ne 100 -or $bytes[1] -lt 64 -or $bytes[1] -gt 127) {throw 'Expected device tailnet IPv4 address'}
if ($Port -lt 1 -or $Port -gt 65535) {throw 'Invalid port'}
if (-not (Get-NetFirewallRule -Name StopwatchMicroTailnet -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name StopwatchMicroTailnet -DisplayName 'Stopwatch Micro tailnet quota' `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -RemoteAddress $DeviceAddress -InterfaceAlias 'Tailscale' -Profile Any | Out-Null
}
Get-NetFirewallRule -Name StopwatchMicroTailnet | Select-Object Name,Enabled,Direction,Action
