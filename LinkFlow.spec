# -*- mode: python ; coding: utf-8 -*-
# 使用 pcdview 中的 Python 执行: python -m PyInstaller LinkFlow.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

_root = Path(SPEC).resolve().parent

_icon_dir = _root / "icon"
_datas = []
if _icon_dir.is_dir():
    _datas.append((str(_icon_dir), "icon"))

_ps_datas, _ps_binaries, _ps_hidden = collect_all("PySide6")

a = Analysis(
    [str(_root / "main.py")],
    pathex=[str(_root)],
    binaries=_ps_binaries,
    datas=_datas + _ps_datas,
    hiddenimports=list(_ps_hidden),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)
pyz = PYZ(a.pure)

# Windows .exe icon when icon\link.ico exists in repo
_exe_icon = _root / "icon" / "link.ico"
_exe_kw = dict(
    exclude_binaries=True,
    name="LinkFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
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
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LinkFlow",
)
