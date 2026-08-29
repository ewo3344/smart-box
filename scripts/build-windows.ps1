$ErrorActionPreference = "Stop"
$core = Join-Path $PSScriptRoot "..\core"
$dist = Join-Path $PSScriptRoot "..\dist"
$windows = Join-Path $PSScriptRoot "..\windows"
$android = Join-Path $PSScriptRoot "..\android"
$versionFile = Join-Path $PSScriptRoot "..\VERSION"
$androidVersionFile = Join-Path $android "version.properties"
$goBin = "C:\Program Files\Go\bin"
$toolchainFile = Join-Path $PSScriptRoot "..\TOOLCHAIN_VERSION"

if (-not (Test-Path $versionFile)) {
    throw "Product version is missing: $versionFile"
}
$smartVersion = (Get-Content $versionFile -TotalCount 1).Trim()
if ($smartVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$') {
    throw "Invalid product version: $smartVersion"
}
if (-not (Test-Path $androidVersionFile)) {
    throw "Android version properties are missing: $androidVersionFile"
}
$upstreamVersion = ((Get-Content $androidVersionFile | Where-Object { $_ -match '^UPSTREAM_VERSION=' }) -replace '^UPSTREAM_VERSION=', '').Trim()
if ([string]::IsNullOrWhiteSpace($upstreamVersion)) {
    throw "UPSTREAM_VERSION is missing: $androidVersionFile"
}
$releaseLabel = "$smartVersion-core-$upstreamVersion"
$publish = Join-Path $dist "smart-box-$releaseLabel-windows-x64"
$archive = Join-Path $dist "smart-box-$releaseLabel-windows-x64.zip"

if (-not (Test-Path $toolchainFile)) {
    throw "Go toolchain pin is missing: $toolchainFile"
}
$toolchain = (Get-Content $toolchainFile -Raw).Trim()
if ($toolchain -notmatch '^go[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Invalid Go toolchain pin in $toolchainFile`: $toolchain"
}
$env:GOTOOLCHAIN = $toolchain

if (-not (Test-Path (Join-Path $goBin "go.exe"))) {
    throw "Go was not found at $goBin. Install a Go launcher for $toolchain or newer."
}

New-Item -ItemType Directory -Force $publish | Out-Null
$env:Path = "$goBin;" + $env:Path
Push-Location $core
try {
    go build -tags "with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api" -trimpath -ldflags "-X github.com/sagernet/sing-box/constant.Version=$releaseLabel -s -w -buildid=" -o (Join-Path $publish "smart-box-core.exe") .\cmd\sing-box
} finally {
    Pop-Location
}

Push-Location $windows
try {
    dotnet publish -c Release -r win-x64 --self-contained true -o $publish
} finally {
    Pop-Location
}

Compress-Archive -Path (Join-Path $publish "*") -DestinationPath $archive -Force
