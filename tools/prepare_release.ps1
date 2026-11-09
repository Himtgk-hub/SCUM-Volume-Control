param([ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '1.0.0')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv\Scripts\python.exe'
$exe = Join-Path $root 'dist\SCUMVolumeControl.exe'
if (!(Test-Path $exe) -or !(Test-Path $python)) { throw 'Build the project with build.bat first.' }
$out = Join-Path $root 'release'
New-Item -ItemType Directory -Path $out -Force | Out-Null
# A unique staging directory avoids overwriting any existing user files.
$stage = Join-Path $out ('staging-' + [guid]::NewGuid().ToString('N'))
$source = Join-Path $stage 'source'
$binary = Join-Path $stage 'binary'
New-Item -ItemType Directory -Path $source,$binary -Force | Out-Null
foreach ($folder in @('src','resources','tests','tools')) {
    Get-ChildItem (Join-Path $root $folder) -File -Recurse | Where-Object {
        $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -in @('.py','.ps1','.png','.ico','.qss')
    } | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length + 1)
        $destination = Join-Path $source $relative
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
}
$docs = @('README.md','PUBLISHING.md','RELEASE_NOTES.md','THIRD_PARTY_NOTICES.md',
          'AUTO_VOLUME.md','DEBUGGING.md','requirements.txt','build.bat','scum_volume.spec','version_info.txt','.gitignore')
foreach ($file in $docs) { Copy-Item -LiteralPath (Join-Path $root $file) -Destination $source }
Copy-Item -LiteralPath $exe -Destination $binary
foreach ($file in @('README.md','RELEASE_NOTES.md','THIRD_PARTY_NOTICES.md')) {
    Copy-Item -LiteralPath (Join-Path $root $file) -Destination $binary
}
& $python (Join-Path $root 'tools\collect_licenses.py') (Join-Path $binary 'licenses')
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
$sourceZip = Join-Path $out "SCUMVolumeControl-$Version-source.zip"
$binaryZip = Join-Path $out "SCUMVolumeControl-$Version-windows-x64.zip"
# .NET includes dotfiles such as .gitignore (Compress-Archive may omit hidden files).
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($pair in @(@($source,$sourceZip),@($binary,$binaryZip))) {
    if (Test-Path $pair[1]) { throw "Archive already exists: $($pair[1]). Move it before rebuilding." }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($pair[0],$pair[1])
}
$hashes = foreach ($file in @($binaryZip,$sourceZip)) {
    $hash = Get-FileHash -LiteralPath $file -Algorithm SHA256
    "$($hash.Hash.ToLower())  $(Split-Path $file -Leaf)"
}
$hashes | Set-Content (Join-Path $out 'SHA256SUMS.txt') -Encoding ascii
# Delete only this script's uniquely named staging directory.
Remove-Item -LiteralPath $stage -Recurse -Force
Write-Output "Prepared source and Windows archives in $out. Nothing was uploaded."
