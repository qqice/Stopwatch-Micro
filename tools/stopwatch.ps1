[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('doctor', 'deps', 'build', 'backup', 'flash', 'monitor', 'verify', 'bridge', 'package', 'restore')]
    [string]$Action = 'doctor',

    [string]$Port,
    [string]$IdfPath,
    [string]$BackupPath,
    [string]$Version,
    [string]$CodexVersion,
    [string]$CodexPath,
    [ValidateSet('auto', 'bluetooth', 'usb')]
    [string]$Transport = 'auto',
    [switch]$DirectGit,
    [switch]$SkipDeps,
    [switch]$Erase,
    [switch]$Interactive,
    [switch]$AllowOffline,
    [switch]$Once,
    [switch]$ConfirmRestore
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Assert-LastExitCode {
    param([Parameter(Mandatory = $true)][string]$Operation)

    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

function Enable-DirectGit {
    if (-not $DirectGit) {
        return
    }

    # Override user-level proxy settings for this process and child Git
    # processes only. This does not modify the user's global Git config.
    $env:GIT_CONFIG_COUNT = '2'
    $env:GIT_CONFIG_KEY_0 = 'http.proxy'
    $env:GIT_CONFIG_VALUE_0 = ''
    $env:GIT_CONFIG_KEY_1 = 'https.proxy'
    $env:GIT_CONFIG_VALUE_1 = ''
}

function Enter-EspIdf {
    $effectiveIdfPath = $IdfPath
    if (-not $effectiveIdfPath) {
        $effectiveIdfPath = $env:IDF_PATH
    }
    if (-not $effectiveIdfPath) {
        throw 'ESP-IDF path is required. Set IDF_PATH or pass -IdfPath.'
    }
    $effectiveIdfPath = [System.IO.Path]::GetFullPath($effectiveIdfPath)
    $exportScript = Join-Path $effectiveIdfPath 'export.ps1'
    if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
        throw "ESP-IDF export script was not found: $exportScript"
    }

    . $exportScript
    $idfVersion = (& idf.py --version | Out-String).Trim()
    Assert-LastExitCode 'idf.py --version'
    if ($idfVersion -notmatch '5\.5\.4') {
        throw "ESP-IDF 5.5.4 is required; detected: $idfVersion"
    }
    Write-Host "ESP-IDF: $idfVersion"
}

function Resolve-StopwatchPort {
    if ($Port) {
        if ($Port -notmatch '^COM\d+$') {
            throw "Invalid serial port '$Port'; expected COM followed by a number"
        }
        return $Port.ToUpperInvariant()
    }

    $matching = @()
    try {
        $matching = @(
            Get-PnpDevice -PresentOnly -Class Ports -ErrorAction Stop |
                Where-Object { $_.InstanceId -like 'USB\VID_303A&PID_1001*' } |
                ForEach-Object {
                    if ($_.FriendlyName -match '\((COM\d+)\)$') {
                        $Matches[1].ToUpperInvariant()
                    }
                }
        )
    } catch {
        $matching = @()
    }

    $matching = @($matching | Sort-Object -Unique)
    if ($matching.Count -eq 1) {
        return $matching[0]
    }

    $allPorts = @([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object -Unique)
    if ($allPorts.Count -eq 1) {
        return $allPorts[0].ToUpperInvariant()
    }

    throw "Unable to select one ESP32-S3 serial port. Pass -Port COMx. Available: $($allPorts -join ', ')"
}

function Get-StopwatchMac {
    param([Parameter(Mandatory = $true)][string]$ResolvedPort)

    $output = (& python -m esptool --chip esp32s3 --port $ResolvedPort read_mac 2>&1 | Out-String)
    Assert-LastExitCode 'device MAC lookup'
    if ($output -notmatch '(?im)^MAC:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})\s*$') {
        throw "Unable to parse ESP32-S3 MAC address from esptool output: $output"
    }
    return $Matches[1].ToUpperInvariant()
}

function Assert-BackupForDevice {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$DeviceMac
    )

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $backupFile = Get-Item -LiteralPath $resolved
    if ($backupFile.Length -ne 0x1000000) {
        throw "Backup must be exactly 16 MiB: $resolved"
    }
    $metadataPath = "$resolved.json"
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
        throw "Backup metadata is missing: $metadataPath"
    }
    $metadata = Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json
    if ($metadata.chip -ne 'esp32s3' -or $metadata.size -ne '0x1000000') {
        throw "Backup metadata does not describe an ESP32-S3 16 MiB image: $metadataPath"
    }
    if ([string]$metadata.device_mac -ne $DeviceMac) {
        throw "Backup belongs to $($metadata.device_mac), but the connected device is $DeviceMac"
    }
    $actualHash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$metadata.sha256).ToLowerInvariant()) {
        throw "Backup SHA-256 does not match its metadata: $resolved"
    }
    return $resolved
}

function Resolve-VerifiedBackup {
    param([Parameter(Mandatory = $true)][string]$ResolvedPort)

    $deviceMac = Get-StopwatchMac -ResolvedPort $ResolvedPort
    if ($BackupPath) {
        return Assert-BackupForDevice -Path $BackupPath -DeviceMac $deviceMac
    }

    $backupRoot = Join-Path $projectRoot '.artifacts\backups'
    $candidates = @(Get-ChildItem -LiteralPath $backupRoot -Filter '*.bin' -File -Recurse -ErrorAction SilentlyContinue)
    $valid = @()
    foreach ($candidate in $candidates) {
        try {
            $valid += Assert-BackupForDevice -Path $candidate.FullName -DeviceMac $deviceMac
        } catch {
            Write-Warning $_.Exception.Message
        }
    }
    if ($valid.Count -ne 1) {
        throw "Expected exactly one verified backup for $deviceMac; found $($valid.Count). Pass -BackupPath explicitly."
    }
    return $valid[0]
}

function Invoke-DependencyFetch {
    Enable-DirectGit
    & python (Join-Path $projectRoot 'fetch_repos.py')
    Assert-LastExitCode 'dependency fetch'
}

function Invoke-Build {
    Enter-EspIdf
    if (-not $SkipDeps) {
        Invoke-DependencyFetch
    }
    Push-Location $projectRoot
    try {
        & idf.py build
        Assert-LastExitCode 'firmware build'
    } finally {
        Pop-Location
    }
}

function Get-ProjectVersion {
    if ($Version) {
        return $Version
    }

    $cmake = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'CMakeLists.txt')
    if ($cmake -notmatch 'set\(PROJECT_VER\s+"([^"]+)"\)') {
        throw 'Unable to read PROJECT_VER from CMakeLists.txt; pass -Version explicitly'
    }
    return $Matches[1]
}

switch ($Action) {
    'doctor' {
        $resolvedPort = Resolve-StopwatchPort
        Write-Host "StopWatch serial port: $resolvedPort"
        Enter-EspIdf
        & python -m esptool version
        Assert-LastExitCode 'esptool version'
        & python -m esptool --chip esp32s3 --port $resolvedPort flash_id
        Assert-LastExitCode 'device probe'
    }

    'deps' {
        Invoke-DependencyFetch
    }

    'build' {
        Invoke-Build
    }

    'backup' {
        Enter-EspIdf
        $resolvedPort = Resolve-StopwatchPort
        $deviceMac = Get-StopwatchMac -ResolvedPort $resolvedPort
        if (-not $BackupPath) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $backupDirectory = Join-Path $projectRoot ".artifacts\backups\$stamp"
            $BackupPath = Join-Path $backupDirectory 'stopwatch_factory_16MiB.bin'
        } else {
            $BackupPath = [System.IO.Path]::GetFullPath($BackupPath)
            $backupDirectory = Split-Path -Parent $BackupPath
        }
        New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
        if (Test-Path -LiteralPath $BackupPath) {
            throw "Refusing to overwrite an existing backup: $BackupPath"
        }
        $partialPath = "$BackupPath.partial"
        if (Test-Path -LiteralPath $partialPath) {
            throw "A partial backup already exists; inspect or remove it before retrying: $partialPath"
        }

        & python -m esptool --chip esp32s3 --port $resolvedPort --baud 921600 `
            --before default_reset --after hard_reset read_flash 0x0 0x1000000 $partialPath
        Assert-LastExitCode 'full flash backup'

        $backupFile = Get-Item -LiteralPath $partialPath
        if ($backupFile.Length -ne 0x1000000) {
            throw "Backup has unexpected size: $($backupFile.Length) bytes"
        }
        $digest = Get-FileHash -LiteralPath $partialPath -Algorithm SHA256
        Move-Item -LiteralPath $partialPath -Destination $BackupPath
        $backupFile = Get-Item -LiteralPath $BackupPath
        $metadata = [ordered]@{
            created_at = (Get-Date).ToString('o')
            port = $resolvedPort
            chip = 'esp32s3'
            usb_vid = '0x303A'
            usb_pid = '0x1001'
            device_mac = $deviceMac
            start = '0x0'
            size = '0x1000000'
            file = $backupFile.Name
            sha256 = $digest.Hash.ToLowerInvariant()
        }
        $metadataPath = "$BackupPath.json"
        $metadata | ConvertTo-Json | Set-Content -LiteralPath $metadataPath -Encoding utf8
        Write-Host "Backup: $BackupPath"
        Write-Host "SHA-256: $($digest.Hash)"
    }

    'flash' {
        Enter-EspIdf
        $resolvedPort = Resolve-StopwatchPort
        $verifiedBackup = Resolve-VerifiedBackup -ResolvedPort $resolvedPort
        Write-Host "Verified recovery image: $verifiedBackup"
        Push-Location $projectRoot
        try {
            if ($Erase) {
                & idf.py -p $resolvedPort erase-flash
                Assert-LastExitCode 'flash erase'
            }
            & idf.py -p $resolvedPort flash
            Assert-LastExitCode 'firmware flash'
        } finally {
            Pop-Location
        }
    }

    'monitor' {
        Enter-EspIdf
        $resolvedPort = Resolve-StopwatchPort
        Push-Location $projectRoot
        try {
            & idf.py -p $resolvedPort monitor
            Assert-LastExitCode 'serial monitor'
        } finally {
            Pop-Location
        }
    }

    'verify' {
        Enter-EspIdf
        $resolvedPort = Resolve-StopwatchPort
        $arguments = @('-u', (Join-Path $projectRoot 'tools\serial_debug_test.py'), '--port', $resolvedPort)
        if ($Interactive) {
            $arguments += '--interactive'
        }
        if ($AllowOffline) {
            $arguments += '--allow-offline'
        }
        & python @arguments
        Assert-LastExitCode 'serial verification'
    }

    'bridge' {
        if ($Port -and $Transport -eq 'bluetooth') {
            throw '-Port cannot be used with -Transport bluetooth'
        }
        $effectiveTransport = $Transport
        if ($Port -and $effectiveTransport -eq 'auto') {
            $effectiveTransport = 'usb'
        }
        $arguments = @(
            '-u',
            (Join-Path $projectRoot 'tools\stopwatch_bridge.py'),
            '--transport',
            $effectiveTransport
        )
        if ($effectiveTransport -eq 'usb') {
            Enter-EspIdf
            $resolvedPort = Resolve-StopwatchPort
            $arguments += @('--port', $resolvedPort)
        }
        if ($Once) {
            $arguments += '--once'
        }
        if ($CodexPath) {
            $arguments += @('--codex-path', [System.IO.Path]::GetFullPath($CodexPath))
        }
        & python @arguments
        Assert-LastExitCode 'Stopwatch usage bridge'
    }

    'package' {
        if ($SkipDeps) {
            throw '-SkipDeps is not allowed for a release package; pinned dependencies must be verified.'
        }
        $sourceStatus = (& git -C $projectRoot status --porcelain --untracked-files=normal | Out-String).Trim()
        Assert-LastExitCode 'git worktree check'
        if ($sourceStatus) {
            throw "Refusing to package a dirty worktree. Commit the verified source first:`n$sourceStatus"
        }
        Invoke-Build
        $postBuildStatus = (& git -C $projectRoot status --porcelain --untracked-files=normal | Out-String).Trim()
        Assert-LastExitCode 'post-build worktree check'
        if ($postBuildStatus) {
            throw "The build changed tracked source or lock files; review before packaging:`n$postBuildStatus"
        }
        $packageVersion = Get-ProjectVersion
        if (-not $CodexVersion) {
            try {
                $codexPackage = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction Stop |
                    Sort-Object Version -Descending |
                    Select-Object -First 1
                $CodexVersion = $codexPackage.Version.ToString()
            } catch {
                $CodexVersion = 'unverified'
            }
        }
        $commit = (& git -C $projectRoot rev-parse HEAD | Out-String).Trim()
        Assert-LastExitCode 'git revision lookup'
        & python (Join-Path $projectRoot 'tools\package_release.py') `
            --version $packageVersion --commit $commit --codex-version $CodexVersion `
            --build-dir (Join-Path $projectRoot 'build') `
            --output-dir (Join-Path $projectRoot 'dist')
        Assert-LastExitCode 'release packaging'
    }

    'restore' {
        if (-not $ConfirmRestore) {
            throw 'Restore overwrites the complete 16 MB flash. Re-run with -ConfirmRestore.'
        }
        if (-not $BackupPath) {
            throw 'Pass the full factory image with -BackupPath.'
        }
        Enter-EspIdf
        $resolvedPort = Resolve-StopwatchPort
        $resolvedBackup = Resolve-VerifiedBackup -ResolvedPort $resolvedPort
        & python -m esptool --chip esp32s3 --port $resolvedPort --baud 921600 `
            --before default_reset --after hard_reset write_flash 0x0 $resolvedBackup
        Assert-LastExitCode 'factory image restore'
    }
}
