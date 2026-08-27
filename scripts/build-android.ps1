param(
    [string]$GoCommand = "go"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Core = Join-Path $Root "core"
$Android = Join-Path $Root "android"
$Dist = Join-Path $Root "dist"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command was not found on PATH: $Name"
    }
}

function Read-VersionProperty([string]$Name) {
    $properties = Join-Path $Android "version.properties"
    $line = Get-Content $properties | Where-Object { $_ -match "^$([regex]::Escape($Name))=(.+)$" } | Select-Object -First 1
    if (-not $line) {
        throw "Missing $Name in $properties"
    }
    return ($line -replace "^$([regex]::Escape($Name))=", "").Trim()
}

Require-Command $GoCommand
Require-Command "java"
$GoPath = (& $GoCommand env GOPATH).Trim()
if ($LASTEXITCODE -ne 0 -or -not $GoPath) {
    throw "Could not determine GOPATH with $GoCommand env GOPATH"
}
$GoBin = Join-Path $GoPath "bin"
$Gomobile = Join-Path $GoBin "gomobile.exe"
$Gobind = Join-Path $GoBin "gobind.exe"
if (-not (Test-Path $Gomobile) -or -not (Test-Path $Gobind)) {
    throw "gomobile is not initialized. Run: gomobile init"
}
$env:Path = "$GoBin$([IO.Path]::PathSeparator)$env:Path"
if (-not $env:ANDROID_HOME -and -not $env:ANDROID_SDK_ROOT) {
    throw "Android SDK is missing. Set ANDROID_HOME or ANDROID_SDK_ROOT."
}
if (-not (Test-Path (Join-Path $Core ".git")) -or -not (Test-Path (Join-Path $Android ".git"))) {
    throw "A required submodule is not initialized. Run: git submodule update --init --recursive"
}

$SmartVersion = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$UpstreamVersion = Read-VersionProperty "UPSTREAM_VERSION"
$AndroidVersion = Read-VersionProperty "VERSION_NAME"

New-Item -ItemType Directory -Force (Join-Path $Android "app\libs") | Out-Null
New-Item -ItemType Directory -Force $Dist | Out-Null
Push-Location $Core
$HadLibboxVersion = Test-Path Env:SMART_BOX_LIBBOX_VERSION
$PreviousLibboxVersion = $env:SMART_BOX_LIBBOX_VERSION
try {
    $env:SMART_BOX_LIBBOX_VERSION = "smart-box-$SmartVersion-core-$UpstreamVersion"
    & $GoCommand run .\cmd\internal\build_libbox -debug
    if ($LASTEXITCODE -ne 0) { throw "libbox build failed" }
    Copy-Item .\libbox.aar (Join-Path $Android "app\libs\libbox.aar") -Force
    Copy-Item .\libbox-legacy.aar (Join-Path $Android "app\libs\libbox-legacy.aar") -Force
} finally {
    if ($HadLibboxVersion) {
        $env:SMART_BOX_LIBBOX_VERSION = $PreviousLibboxVersion
    } else {
        Remove-Item Env:SMART_BOX_LIBBOX_VERSION -ErrorAction SilentlyContinue
    }
    Pop-Location
}

Push-Location $Android
try {
    .\gradlew.bat assemblePlayDebug
    if ($LASTEXITCODE -ne 0) { throw "Android Gradle build failed" }
} finally {
    Pop-Location
}

$ApkDirectory = Join-Path $Android "app\build\outputs\apk\play\debug"
$Universal = Join-Path $ApkDirectory "smart-box-$AndroidVersion-universal-debug.apk"
$Arm64 = Join-Path $ApkDirectory "smart-box-$AndroidVersion-arm64-v8a-debug.apk"
if (-not (Test-Path $Universal) -or -not (Test-Path $Arm64)) {
    throw "Expected Play debug APKs were not produced in $ApkDirectory"
}

$UniversalOutput = Join-Path $Dist "smart-box-$SmartVersion-core-$UpstreamVersion-android-universal.apk"
$Arm64Output = Join-Path $Dist "smart-box-$SmartVersion-core-$UpstreamVersion-android-arm64.apk"
Copy-Item $Universal $UniversalOutput -Force
Copy-Item $Arm64 $Arm64Output -Force
Write-Output "ANDROID_VERSION=$AndroidVersion"
Write-Output "ANDROID_UNIVERSAL_APK=$UniversalOutput"
Write-Output "ANDROID_ARM64_APK=$Arm64Output"
