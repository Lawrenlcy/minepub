# Install the official self-contained calibre runtime inside this project.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$installerPath = Join-Path $PSScriptRoot 'tmp\calibre-portable-installer.exe'
$calibrePath = Join-Path $PSScriptRoot 'tools\calibre'
$converterPath = Join-Path $calibrePath 'Calibre Portable\Calibre\ebook-convert.exe'
if (Test-Path -LiteralPath $converterPath) {
    & $converterPath --version
    exit $LASTEXITCODE
}
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'tmp'), (Join-Path $PSScriptRoot 'tools') | Out-Null
if (-not (Test-Path -LiteralPath $installerPath)) {
    & curl.exe -L --fail --retry 2 --output $installerPath 'https://calibre-ebook.com/dist/portable'
    if ($LASTEXITCODE -ne 0) { throw 'Download failed. Remove tmp\calibre-portable-installer.exe and retry.' }
}
$signature = Get-AuthenticodeSignature -LiteralPath $installerPath
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Kovid Goyal(?:,|$)') {
    throw 'The calibre installer signature is not valid. Download again from https://calibre-ebook.com/download_portable.'
}
$installer = Start-Process -FilePath $installerPath -ArgumentList ('"' + $calibrePath + '"') -WindowStyle Hidden -PassThru -Wait
if ($installer.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $converterPath)) {
    throw 'Calibre setup did not complete. Check tools\calibre.'
}
& $converterPath --version
Write-Host 'AZW3 conversion is ready. Refresh Minepub to use it.'
