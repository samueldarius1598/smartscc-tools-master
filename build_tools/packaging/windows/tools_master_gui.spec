# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project_root = Path(SPEC).resolve().parents[3]
build_tools = project_root / "build_tools"

datas = []
for package_name in ("requests", "httpx", "openpyxl", "rich", "certifi", "tkcalendar", "babel"):
    try:
        datas += copy_metadata(package_name)
    except Exception:
        pass

for data_dir_name in ("assets", "icon"):
    data_dir = build_tools / data_dir_name
    if data_dir.exists():
        datas.append((str(data_dir), data_dir_name))

hiddenimports = (
    collect_submodules("smartscc_tools.features.item_journal")
    + collect_submodules("smartscc_tools")
    + collect_submodules("smartscc_tools.features.edit_transaksi")
    + collect_submodules("smartscc_tools.features.svl_fix_je")
    + collect_submodules("smartscc_tools.features.update_std_cost")
    + collect_submodules("tkcalendar")
    + collect_submodules("babel")
)

icon_path = build_tools / "icon" / "mainlogo.ico"

a = Analysis(
    [str(build_tools / "packaging" / "windows" / "gui_launcher.py")],
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
    name="smartscc_tools_master",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="smartscc_tools_master",
)
