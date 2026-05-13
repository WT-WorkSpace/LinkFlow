# -*- mode: python ; coding: utf-8 -*-
# 勿使用 collect_all('PySide6')，否则会打入 WebEngine/3D/多媒体等整块 Qt，体积可达 1GB+。
# 仅打包 main.py 实际用到的模块，由 PyInstaller 的 hook-PySide6 按依赖收集 Qt 库与必要插件。
from pathlib import Path

_root = Path(SPEC).resolve().parent

_icon_dir = _root / "icon"
_datas = []
if _icon_dir.is_dir():
    _datas.append((str(_icon_dir), "icon"))

a = Analysis(
    [str(_root / "main.py")],
    pathex=[str(_root)],
    binaries=[],
    datas=_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)
pyz = PYZ(a.pure)

_exe_icon = _root / "icon" / "link.ico"
_exe_kw = dict(
    exclude_binaries=True,
    name="LinkFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
if _exe_icon.is_file():
    _exe_kw["icon"] = str(_exe_icon)

exe = EXE(pyz, a.scripts, [], **_exe_kw)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=True,
    upx=False,
    upx_exclude=[],
    name="LinkFlow",
)
