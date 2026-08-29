$ErrorActionPreference = "Stop"
$android = Join-Path $PSScriptRoot "..\android"
$core = Join-Path $PSScriptRoot "..\core"
$dist = Join-Path $PSScriptRoot "..\dist"
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
$androidProperties = Get-Content $androidVersionFile
$versionName = (($androidProperties | Where-Object { $_ -match '^VERSION_NAME=' }) -replace '^VERSION_NAME=', '').Trim()
$upstreamVersion = (($androidProperties | Where-Object { $_ -match '^UPSTREAM_VERSION=' }) -replace '^UPSTREAM_VERSION=', '').Trim()
if ([string]::IsNullOrWhiteSpace($versionName) -or [string]::IsNullOrWhiteSpace($upstreamVersion)) {
    throw "VERSION_NAME/UPSTREAM_VERSION is missing: $androidVersionFile"
}
$releaseLabel = "$smartVersion-core-$upstreamVersion"

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
if (-not (Test-Path (Join-Path $env:USERPROFILE "go\bin\gomobile.exe"))) {
    throw "gomobile is missing. Run: go install golang.org/x/mobile/cmd/gomobile@latest"
}
if (-not $env:ANDROID_HOME) {
    throw "Android SDK is missing. Set ANDROID_HOME to an SDK containing NDK 28.0.13004108."
}
$defaultJdk = "C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot"
if (-not $env:JAVA_HOME -and (Test-Path (Join-Path $defaultJdk "bin\java.exe"))) {
    $env:JAVA_HOME = $defaultJdk
}
if (-not $env:JAVA_HOME) {
    throw "Java 17 is missing. Set JAVA_HOME to a JDK 17 installation."
}

$env:Path = "$goBin;" + $env:Path
New-Item -ItemType Directory -Force (Join-Path $android "app\libs") | Out-Null
Push-Location $core
try {
    go run .\cmd\internal\build_libbox -debug
    Copy-Item .\libbox.aar (Join-Path $android "app\libs\libbox.aar") -Force
    Copy-Item .\libbox-legacy.aar (Join-Path $android "app\libs\libbox-legacy.aar") -Force
} finally {
    Pop-Location
}

Push-Location $android
try {
    .\gradlew.bat assemblePlayDebug
    New-Item -ItemType Directory -Force $dist | Out-Null
    $apkDir = Join-Path $android "app\build\outputs\apk\play\debug"
    $apkPrefix = "smart-box-$versionName"
    Copy-Item (Join-Path $apkDir "$apkPrefix-play-universal-debug.apk") (Join-Path $dist "smart-box-$releaseLabel-android-universal.apk") -Force
    Copy-Item (Join-Path $apkDir "$apkPrefix-play-arm64-v8a-debug.apk") (Join-Path $dist "smart-box-$releaseLabel-android-arm64.apk") -Force
} finally {
    Pop-Location
}
