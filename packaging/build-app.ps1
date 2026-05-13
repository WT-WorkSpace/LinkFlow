#requires -Version 5.1
# PyInstaller 打包为 dist\LinkFlow\（onedir，无控制台窗口）。在「以 Windows 构建」的机器上运行。
$ErrorActionPreference = 'Stop'

# 本机构建使用的 Python：把下面默认路径改成你的 python.exe；也可用环境变量 LINKFLOW_BUILD_PYTHON 覆盖。
$DefaultPython = "$env:USERPROFILE\miniconda3\envs\pcdview\python.exe"
$Python = if ($env:LINKFLOW_BUILD_PYTHON) { $env:LINKFLOW_BUILD_PYTHON } else { $DefaultPython }
 
$ScriptDir = $PSScriptRoot
$Root = (Resolve-Path (Join-Path $ScriptDir '..')).Path

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "解释器不存在或不可访问: $Python"
}

Write-Host "使用解释器: $Python"
Set-Location -LiteralPath $Root

& $Python -m pip install -q 'pyinstaller>=6.0'
& $Python -m PyInstaller --noconfirm (Join-Path $Root 'LinkFlow.spec')

$AppDir = Join-Path $Root 'dist\LinkFlow'
$Exe = Join-Path $AppDir 'LinkFlow.exe'
if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Error "未生成可执行文件: $Exe"
}

$internalIcon = Join-Path $AppDir '_internal\icon'
if (Test-Path -LiteralPath $internalIcon) {
    $outIcon = Join-Path $AppDir 'icon'
    if (Test-Path -LiteralPath $outIcon) {
        Remove-Item -LiteralPath $outIcon -Recurse -Force
    }
    Copy-Item -LiteralPath $internalIcon -Destination $outIcon -Recurse -Force
}

$hostsSrc = Join-Path $Root 'hosts.json'
if (Test-Path -LiteralPath $hostsSrc) {
    Copy-Item -LiteralPath $hostsSrc -Destination (Join-Path $AppDir 'hosts.json') -Force
}

$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop 'LinkFlow.lnk'
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnkPath)
$s.TargetPath = $Exe
$s.WorkingDirectory = $AppDir
$s.Description = 'LinkFlow'
$s.Save()

Write-Host ''
Write-Host "应用已生成: $AppDir"
Write-Host "  可执行文件: $Exe"
Write-Host "  桌面快捷方式: $lnkPath"
Write-Host '分发整个 dist\LinkFlow 文件夹即可；移动目录后请重新运行本脚本以更新快捷方式。'
