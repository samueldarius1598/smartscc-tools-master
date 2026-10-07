# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project_root = Path(SPEC).resolve().parents[2]

datas = []
for package_name in ("requests", "httpx", "openpyxl", "rich", "certifi"):
    try:
        datas += copy_metadata(package_name)
    except Exception:
        pass

hiddenimports = (
    collect_submodules("smartscc_tools")
    + collect_submodules("smartscc_tools.features.item_journal")
)

a = Analysis(
    [str(project_root / "packaging" / "windows" / "gui_launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="internal_transfer_uploader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="internal_transfer_uploader",
)
