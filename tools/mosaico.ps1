param(
 [ValidateSet('build','probe','flash')][string]$Action='build',
 [string]$Port='',
 [switch]$AllowOverwriteWithoutBackup,
 [string]$Sdk='C:\esp\v6.1\esp-idf',
 [string]$Python='C:\Espressif\tools\python\v6.1\venv\Scripts\python.exe'
)
$ErrorActionPreference='Stop'
if($Action -eq 'flash' -and !$AllowOverwriteWithoutBackup) {
 throw 'This wrapper does not create a full app backup. Prepare a verified rollback image separately; this specific overwrite path requires explicit -AllowOverwriteWithoutBackup consent.'
}
$root=Split-Path $PSScriptRoot -Parent
$project=Join-Path $root 'boards\mosaico'
$build=Join-Path $root '.artifacts\mosaico\build'
$private=Join-Path $root '.artifacts\private\mosaico'
New-Item -ItemType Directory -Force $private | Out-Null
& 'C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1' -e 6>&1 | ForEach-Object {
 if ($_ -match '^([^=]+)=(.*)$'){Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]}
}
$env:IDF_PATH=$Sdk
$env:IDF_PY_BUILD_JOBS='2'; $env:CMAKE_BUILD_PARALLEL_LEVEL='1'; $env:PYTHONUTF8='1'
$idf=Join-Path $Sdk 'tools\idf.py'
if ($Action -ne 'probe') {
 & $Python $idf --preview -C $project -B $build '-DIDF_TARGET=esp32s31' "-DSDKCONFIG=$root/.artifacts/mosaico/sdkconfig" build
 if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
}
if ($Action -eq 'build'){exit 0}
# The owner must enter download mode; never guess a Bluetooth/unrelated port.
$ports=@(Get-PnpDevice -PresentOnly -Class Ports | Where-Object {$_.InstanceId -like 'USB\VID_303A*'} | ForEach-Object {
 if($_.FriendlyName -match '\((COM\d+)\)$'){$Matches[1]}
})
if(!$Port){if($ports.Count -ne 1){throw 'Select the identified Mosaico download port with -Port.'};$Port=$ports[0]}
if($Port -notin $ports){throw 'Port is not a currently present Espressif USB device.'}
& $Python -m esptool --chip esp32s31 --port $Port --before no-reset --after no-reset chip-id
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$prefix=Join-Path $private "protected-prefix-$stamp.bin"
& $Python -m esptool --chip esp32s31 --port $Port --before no-reset --after no-reset read-flash 0 0xa000 $prefix
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
if($Action -eq 'probe'){Write-Output "Protected layout captured privately: $prefix";exit 0}
$cfg=Get-Content (Join-Path $build 'config\sdkconfig.h') -Raw
foreach($flag in @('CONFIG_SPIRAM_XIP_FROM_PSRAM','CONFIG_TINYUSB_CDC_ENABLED','CONFIG_IDF_TARGET_ESP32S31')) {
 if($cfg -notmatch "#define $flag 1"){throw "Refusing missing $flag"}
}
$security=(& $Python -m esptool --chip esp32s31 --port $Port --before no-reset --after no-reset get-security-info 2>&1 | Out-String)
if($LASTEXITCODE -ne 0 -or $security -notmatch 'Secure Boot: Disabled' -or $security -notmatch 'Flash Encryption: Disabled') {
 throw 'Security state not validated; do not change keys/eFuses or use an unsigned image.'
}
$image=Join-Path $build 'Stopwatch-Mosaico.bin'
& $Python (Join-Path $PSScriptRoot 'validate_mosaico_image.py') --prefix $prefix --image $image --table (Join-Path $build 'partition_table\partition-table.bin') --idf $Sdk --bootloader-sha256 cf58a33f53a27e66fce9456a6f28f964524247571399d5335b379738e4cd739a
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
# Explicit app-only offset. NEVER substitute idf.py flash or @flash_args here.
& $Python -m esptool --chip esp32s31 --port $Port --before no-reset --after hard-reset write-flash 0x20000 $image
exit $LASTEXITCODE
