param(
    [string]$GoCommand = "go"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Core = Join-Path $Root "core"
$WindowsClient = Join-Path $Root "windows"
$Dist = Join-Path $Root "dist"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command was not found on PATH: $Name"
    }
}

function Read-VersionProperty([string]$Name) {
    $properties = Join-Path $Root "android\version.properties"
    $line = Get-Content $properties | Where-Object { $_ -match "^$([regex]::Escape($Name))=(.+)$" } | Select-Object -First 1
    if (-not $line) {
        throw "Missing $Name in $properties"
    }
    return ($line -replace "^$([regex]::Escape($Name))=", "").Trim()
}

Require-Command $GoCommand
Require-Command "dotnet"
if (-not (Test-Path (Join-Path $Core ".git"))) {
    throw "The core submodule is not initialized. Run: git submodule update --init --recursive"
}

$SmartVersion = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$UpstreamVersion = Read-VersionProperty "UPSTREAM_VERSION"
$Publish = Join-Path $Dist "smart-box-$SmartVersion-core-$UpstreamVersion-windows-x64"
$Archive = "$Publish.zip"

New-Item -ItemType Directory -Force $Publish | Out-Null
Push-Location $Core
try {
    $ldflags = "-X github.com/sagernet/sing-box/constant.Version=smart-box-$SmartVersion-core-$UpstreamVersion -s -w -buildid="
    & $GoCommand build -tags "with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api" -trimpath -ldflags $ldflags -o (Join-Path $Publish "smart-box-core.exe") .\cmd\sing-box
    if ($LASTEXITCODE -ne 0) { throw "sing-box core build failed" }
} finally {
    Pop-Location
}

Push-Location $WindowsClient
try {
    dotnet publish -c Release -r win-x64 --self-contained true -o $Publish
    if ($LASTEXITCODE -ne 0) { throw "Windows client publish failed" }
} finally {
    Pop-Location
}

if (Test-Path $Archive) {
    Remove-Item -Force $Archive
}
Compress-Archive -Path (Join-Path $Publish "*") -DestinationPath $Archive -Force
Write-Output "WINDOWS_PACKAGE=$Publish"
Write-Output "WINDOWS_ARCHIVE=$Archive"
