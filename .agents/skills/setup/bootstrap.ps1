# Installs the Python these skills run on, inside this project folder only.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .agents/skills/setup/bootstrap.ps1
#
# Downloads a pinned release of the official python.org "embeddable" package
# (signed by the Python Software Foundation), checks its SHA-256 against the
# value below -- the same digest python.org publishes in the release's sigstore
# bundle -- and unpacks it into runtime/python/. Nothing outside this folder
# changes: no admin rights, no PATH, no registry. Deleting runtime/ uninstalls it.
# Re-running is harmless: a matching Python already in place is left as is.
#
# -Zip <file> uses an already downloaded package instead (it is still checked).
param([string]$Zip = "")

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Windows PowerShell 5.1 downloads 10x faster without the progress bar

$Version = "3.14.8"
$Sha256 = @{
    "amd64" = "a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310"
    "arm64" = "155be84ccb57c6331cf0e39001c78a1dfac3be62f403f3ff5f2e29b80dda7ebe"
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Runtime = Join-Path $Root "runtime"
$Target = Join-Path $Runtime "python"
$Exe = Join-Path $Target "python.exe"

function Done($msg) { Write-Output "OK   $msg"; exit 0 }
function Fail($msg, $code) { Write-Output "TODO $msg"; exit $code }

$arch = $env:PROCESSOR_ARCHITEW6432
if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
$arch = $arch.ToLower()
if (-not $Sha256.ContainsKey($arch)) {
    Fail "this computer's processor ($arch) is not supported: a 64-bit Windows is needed" 2
}

if (Test-Path $Exe) {
    $have = (& $Exe --version 2>&1 | Out-String).Trim()
    if ($have -eq "Python $Version") { Done "Python $Version is already set up in runtime\python (only for this folder)" }
}

$name = "python-$Version-embed-$arch.zip"
$tmp = $null
if (-not $Zip) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) $name
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$Version/$name" -OutFile $tmp
    } catch {
        Fail "could not download Python from python.org -- check the internet connection and try again ($($_.Exception.Message))" 1
    }
    $Zip = $tmp
}
if (-not (Test-Path $Zip)) { Fail "no such file: $Zip" 2 }

$got = (Get-FileHash -Algorithm SHA256 -Path $Zip).Hash.ToLower()
if ($got -ne $Sha256[$arch]) {
    if ($tmp) { Remove-Item -Force $tmp }
    Fail "the downloaded Python does not match the expected checksum, so it was NOT installed. Try again later; if it keeps happening, the download is being altered on the way." 2
}

$new = Join-Path $Runtime "python.new"
if (Test-Path $new) { Remove-Item -Recurse -Force $new }
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
Expand-Archive -Path $Zip -DestinationPath $new
if ($tmp) { Remove-Item -Force $tmp }

$test = (& (Join-Path $new "python.exe") -c "import ssl, json, http.server, urllib.request; print('ok')" 2>&1 | Out-String).Trim()
if ($test -ne "ok") {
    Remove-Item -Recurse -Force $new
    Fail "the new Python did not start ($test). Windows security software may have blocked it." 1
}
if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
Rename-Item -Path $new -NewName "python"
Done "Python $Version is set up in runtime\python (only for this folder; delete that folder to remove it)"
