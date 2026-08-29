param(
    [string]$OutputDirectory = (Join-Path (Join-Path $PSScriptRoot '..') 'verification/windows-verify'),
    [switch]$AllowMissing,
    [switch]$SkipPublish
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$windowsProject = Join-Path $root 'windows/SingBoxSmart.Windows.csproj'
$coreProject = Join-Path $root 'core'
$failed = [System.Collections.Generic.List[object]]::new()
$blocked = [System.Collections.Generic.List[object]]::new()
$passed = [System.Collections.Generic.List[object]]::new()

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

function Add-Result {
    param([string]$Name, [string]$Status, [string]$Message)
    $entry = [pscustomobject]@{ Name = $Name; Status = $Status; Message = $Message }
    switch ($Status) {
        'PASS' { $passed.Add($entry) }
        'BLOCKED' { $blocked.Add($entry) }
        default { $failed.Add($entry) }
    }
    Write-Host ("{0,-22} {1} {2}" -f $Name, $Status, $Message)
}

function Invoke-Check {
    param([string]$Name, [scriptblock]$Action)
    $log = Join-Path $OutputDirectory "$Name.log"
    try {
        & $Action *>&1 | Tee-Object -FilePath $log
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            Add-Result $Name "FAIL($LASTEXITCODE)" "command returned $LASTEXITCODE"
        } else {
            Add-Result $Name 'PASS' "log: $(Split-Path $log -Leaf)"
        }
    } catch {
        $_ | Out-File -FilePath $log -Encoding utf8
        Add-Result $Name 'FAIL' $_.Exception.Message
    }
}

if (-not (Test-Path $windowsProject)) {
    Add-Result 'project' 'FAIL' "missing $windowsProject"
} else {
    try {
        [xml]$projectXml = Get-Content -Raw -LiteralPath $windowsProject
        $target = $projectXml.Project.PropertyGroup.TargetFramework | Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace($target)) {
            Add-Result 'project_metadata' 'FAIL' 'TargetFramework is missing'
        } else {
            Add-Result 'project_metadata' 'PASS' "TargetFramework=$target"
        }
    } catch {
        Add-Result 'project_metadata' 'FAIL' $_.Exception.Message
    }
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Add-Result 'dotnet_available' 'BLOCKED' '.NET SDK is not installed'
} else {
    Invoke-Check 'dotnet_info' { dotnet --info }
    Invoke-Check 'dotnet_build' { dotnet build $windowsProject --configuration Release --no-restore }
    if (-not $SkipPublish) {
        $publish = Join-Path $OutputDirectory 'publish'
        Invoke-Check 'dotnet_publish' { dotnet publish $windowsProject --configuration Release --runtime win-x64 --self-contained false --output $publish }
        if (Test-Path $publish) {
            $app = Get-ChildItem -LiteralPath $publish -Filter '*.dll' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -eq $app) {
                Add-Result 'publish_manifest' 'FAIL' 'publish directory has no application assembly'
            } else {
                Add-Result 'publish_manifest' 'PASS' "found $($app.Name)"
            }
        }
    }
}

$testProjects = Get-ChildItem -Path $root -Filter '*Tests.csproj' -File -Recurse -ErrorAction SilentlyContinue
if ($testProjects.Count -eq 0) {
    Add-Result 'dotnet_tests' 'BLOCKED' 'no *Tests.csproj exists; add unit tests before release'
} elseif (Get-Command dotnet -ErrorAction SilentlyContinue) {
    foreach ($test in $testProjects) {
        $safe = ($test.BaseName -replace '[^A-Za-z0-9_.-]', '_')
        Invoke-Check "test_$safe" { dotnet test $test.FullName --configuration Release --no-restore }
    }
}

if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    Add-Result 'core_toolchain' 'BLOCKED' 'Go is not installed'
} elseif (-not (Test-Path $coreProject)) {
    Add-Result 'core_toolchain' 'FAIL' 'core directory is missing'
} else {
    $toolchain = 'go1.26.5'
    $toolchainFile = Join-Path $root 'TOOLCHAIN_VERSION'
    if (Test-Path $toolchainFile) { $toolchain = (Get-Content -LiteralPath $toolchainFile -TotalCount 1).Trim() }
    Invoke-Check 'core_go_test' { Push-Location $coreProject; try { $env:GOTOOLCHAIN = $toolchain; go test ./... } finally { Pop-Location } }
}

Add-Result 'gui_automation' 'BLOCKED' 'WinAppDriver/UI Automation runner is not configured'
Add-Result 'proxy_e2e' 'BLOCKED' 'requires an isolated Windows network VM'

$report = Join-Path $OutputDirectory 'REPORT.md'
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add('# Windows verification')
$lines.Add("")
$lines.Add("Generated: $(Get-Date -Format o)")
$lines.Add("")
$lines.Add('| Check | Status | Message |')
$lines.Add('| --- | --- | --- |')
foreach ($item in @($passed + $failed + $blocked)) {
    $message = ($item.Message -replace '\|', '\\|')
    $lines.Add("| ``$($item.Name)`` | **$($item.Status)** | $message |")
}
$lines.Add("")
$lines.Add("Failed: **$($failed.Count)**; Blocked: **$($blocked.Count)**")
$lines | Set-Content -LiteralPath $report -Encoding utf8

Write-Host "Report: $report"
if ($failed.Count -gt 0) { exit 1 }
if ($blocked.Count -gt 0 -and -not $AllowMissing) { exit 2 }
exit 0
