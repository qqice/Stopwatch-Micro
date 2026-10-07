# Extract a personally licensed official Store component without running its
# GUI or loading its assembly/DLL. Do not redistribute the resulting binary.
[CmdletBinding()]
param([string]$OutputDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\.artifacts\iotpower\native')))
$pkg=@(Get-AppxPackage -Name '800948F61A16.IOTPower')
if($pkg.Count -ne 1 -or $pkg[0].SignatureKind.ToString() -ne 'Store') {
 throw 'Install the official licensed PowerAnalyzer Store package first; no other publisher is accepted.'
}
$p=$pkg[0].InstallLocation
$exe=Join-Path $p 'IOT Power\PowerAnalyzer.exe'
$s=[IO.File]::OpenRead($exe);$pe=[System.Reflection.PortableExecutable.PEReader]::new($s)
$md=[System.Reflection.Metadata.PEReaderExtensions]::GetMetadataReader($pe)
$block=$pe.GetSectionData($pe.PEHeaders.CorHeader.ResourcesDirectory.RelativeVirtualAddress)
foreach($h in $md.ManifestResources){$r=$md.GetManifestResource($h);$name=$md.GetString($r.Name)
 if($name -eq 'costura64.iot_parser.dll' -and $r.Implementation.IsNil){
  $reader=$block.GetReader();$reader.Offset=[int]$r.Offset;$len=$reader.ReadInt32();$bytes=$reader.ReadBytes($len)
  $out=Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) 'iot_parser.dll'
  New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
  $digest=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
  if(Test-Path -LiteralPath $out) {
   if((Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash -ne $digest){throw 'Existing component differs; choose a new output directory.'}
  } else {
   $file=[IO.File]::Open($out,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
   try{$file.Write($bytes,0,$bytes.Length)} finally{$file.Dispose()}
  }
  Write-Output "EXTRACTED_NATIVE_BYTES $len SHA256=$digest"
 }
}
foreach($h in $md.MethodDefinitions){$m=$md.GetMethodDefinition($h);$n=$md.GetString($m.Name)
 if($n -in @('iot_uart_open','iot_uart_send_initial','iot_uart_request_close','iot_parse','iot_get_current','iot_get_voltage','iot_get_power_on')){
  $imp=$m.GetImport();$sig=$md.GetBlobBytes($m.Signature);Write-Output "ABI $n attrs=$($imp.Attributes) signature=$([Convert]::ToHexString($sig))"
 }
}
$pe.Dispose();$s.Dispose()
Get-FileHash -LiteralPath $out -Algorithm SHA256 | Select-Object Hash,Path
