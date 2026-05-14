# Creates LinkFlow.lnk on the current user's Desktop (respects OneDrive Desktop when configured).
param(
    [Parameter(Mandatory = $true)]
    [string] $AppDir
)

$ErrorActionPreference = 'Stop'

function Resolve-DesktopPath {
    $candidates = @(
        [Environment]::GetFolderPath('Desktop')
        (Join-Path $env:USERPROFILE 'Desktop')
    )
    if ($env:OneDrive) {
        $candidates += (Join-Path $env:OneDrive 'Desktop')
    }
    if ($env:OneDriveCommercial) {
        $candidates += (Join-Path $env:OneDriveCommercial 'Desktop')
    }
    foreach ($p in $candidates) {
        if ([string]::IsNullOrWhiteSpace($p)) { continue }
        if (Test-Path -LiteralPath $p) { return (Resolve-Path -LiteralPath $p).Path }
    }
    throw "Could not resolve Desktop folder. Tried: $($candidates -join '; ')"
}

$AppDir = (Resolve-Path -LiteralPath $AppDir).Path
$exe = Join-Path $AppDir 'LinkFlow.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Executable not found: $exe"
}

$desktop = Resolve-DesktopPath
$lnkPath = Join-Path $desktop 'LinkFlow.lnk'

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnkPath)
$sc.TargetPath = $exe
$sc.WorkingDirectory = $AppDir
$icon = Join-Path $AppDir 'icon\link.ico'
if (Test-Path -LiteralPath $icon) {
    $sc.IconLocation = "$icon,0"
}
$sc.Description = 'LinkFlow'
$sc.Save()

Write-Host "[LinkFlow] Shortcut created: $lnkPath"
exit 0
