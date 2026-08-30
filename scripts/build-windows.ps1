param(
    [string]$GoCommand = "go"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Core = Join-Path $Root "core"
$WindowsClient = Join-Path $Root "windows"
$Dist = Join-Path $Root "dist"
$ToolchainFile = Join-Path $Root "TOOLCHAIN_VERSION"

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
if ($SmartVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$') {
    throw "Invalid product version: $SmartVersion"
}
if (-not (Test-Path $ToolchainFile)) {
    throw "Go toolchain pin is missing: $ToolchainFile"
}
$Toolchain = (Get-Content $ToolchainFile -Raw).Trim()
if ($Toolchain -notmatch '^go[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Invalid Go toolchain pin: $Toolchain"
}
$env:GOTOOLCHAIN = $Toolchain
$UpstreamVersion = Read-VersionProperty "UPSTREAM_VERSION"
$Publish = Join-Path $Dist "smart-box-$SmartVersion-core-$UpstreamVersion-windows-x64"
$Archive = "$Publish.zip"
$Staging = Join-Path $Dist (".smart-box-windows-" + [Guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Force $Dist | Out-Null
New-Item -ItemType Directory -Force $Staging | Out-Null
try {
Push-Location $Core
try {
    $previousGoos = $env:GOOS
    $previousGoarch = $env:GOARCH
    $previousCgo = $env:CGO_ENABLED
    $env:GOOS = "windows"
    $env:GOARCH = "amd64"
    $env:CGO_ENABLED = "0"
    $ldflags = "-X github.com/sagernet/sing-box/constant.Version=smart-box-$SmartVersion-core-$UpstreamVersion -s -w -buildid="
    & $GoCommand build -tags "with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api" -trimpath -ldflags $ldflags -o (Join-Path $Staging "smart-box-core.exe") .\cmd\sing-box
    if ($LASTEXITCODE -ne 0) { throw "sing-box core build failed" }
} finally {
    if ($null -eq $previousGoos) { Remove-Item Env:GOOS -ErrorAction SilentlyContinue } else { $env:GOOS = $previousGoos }
    if ($null -eq $previousGoarch) { Remove-Item Env:GOARCH -ErrorAction SilentlyContinue } else { $env:GOARCH = $previousGoarch }
    if ($null -eq $previousCgo) { Remove-Item Env:CGO_ENABLED -ErrorAction SilentlyContinue } else { $env:CGO_ENABLED = $previousCgo }
    Pop-Location
}

Push-Location $WindowsClient
try {
    dotnet publish -c Release -r win-x64 --self-contained true -p:EnableWindowsTargeting=true -o $Staging
    if ($LASTEXITCODE -ne 0) { throw "Windows client publish failed" }
} finally {
    Pop-Location
}

$windowsReadme = Join-Path $WindowsClient "README.md"
$configDir = Join-Path $WindowsClient "config"
if (-not (Test-Path $windowsReadme)) { throw "missing Windows package README: $windowsReadme" }
if (-not (Test-Path $configDir)) { throw "missing Windows config templates: $configDir" }
Copy-Item -LiteralPath $windowsReadme -Destination (Join-Path $Staging "README.md") -Force
Copy-Item -LiteralPath $configDir -Destination (Join-Path $Staging "config") -Recurse -Force

if (Test-Path $Publish) {
    Remove-Item -Recurse -Force $Publish
}
Move-Item -LiteralPath $Staging -Destination $Publish
if (Test-Path $Archive) {
    Remove-Item -Force $Archive
}
Compress-Archive -Path (Join-Path $Publish "*") -DestinationPath $Archive -Force
$ChecklistArchive = Join-Path $Dist "smart-box-$SmartVersion-windows-x64.zip"
Copy-Item -LiteralPath $Archive -Destination $ChecklistArchive -Force
Write-Output "WINDOWS_PACKAGE=$Publish"
Write-Output "WINDOWS_ARCHIVE=$Archive"
Write-Output "WINDOWS_CHECKLIST_ARCHIVE=$ChecklistArchive"
} finally {
    if (Test-Path $Staging) {
        Remove-Item -Recurse -Force $Staging
    }
}
