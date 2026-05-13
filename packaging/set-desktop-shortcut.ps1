#requires -Version 5.1
# Create Desktop\LinkFlow.lnk -> dist\LinkFlow\LinkFlow.exe with icon\link.ico when present.
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir
)
$ErrorActionPreference = 'Stop'

$AppDir = (Resolve-Path -LiteralPath $AppDir).Path
$ExeName = 'LinkFlow.exe'
$Exe = Join-Path $AppDir $ExeName
if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Error "Missing exe: $Exe"
}

$desktop = [Environment]::GetFolderPath('Desktop')
$lnkName = [System.IO.Path]::GetFileNameWithoutExtension($ExeName) + '.lnk'
$lnkPath = Join-Path $desktop $lnkName

$icon = Join-Path $AppDir 'icon\link.ico'
if (-not (Test-Path -LiteralPath $icon)) {
    $icon = $null
}

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnkPath)
$sc.TargetPath = $Exe
$sc.WorkingDirectory = $AppDir
$sc.Description = 'LinkFlow'
if ($null -ne $icon) {
    $sc.IconLocation = $icon
}
$sc.Save()

Write-Host "Desktop shortcut: $lnkPath"
