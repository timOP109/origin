# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, get_package_paths


project_root = Path(SPECPATH).parent
source_root = project_root / "src"

datas = [
    (str(project_root / "README.md"), "."),
]
datas += collect_data_files(
    "direct_infusion_quant", includes=["resources/*"]
)
datas += collect_data_files("pymzml")
_, pymzml_package = get_package_paths("pymzml")
datas.append((str(Path(pymzml_package) / "obo"), "obo"))
datas += collect_data_files(
    "PySide6",
    includes=[
        "Qt/plugins/platforms/*",
        "Qt/plugins/imageformats/*",
        "Qt/plugins/styles/*",
    ],
)

pyopenms_datas, pyopenms_binaries, pyopenms_hiddenimports = collect_all("pyopenms")
datas += pyopenms_datas

a = Analysis(
    [str(source_root / "direct_infusion_quant" / "app.py")],
    pathex=[str(source_root)],
    binaries=pyopenms_binaries,
    datas=datas,
    hiddenimports=[
        "direct_infusion_quant.io._pyopenms_worker",
        "direct_infusion_quant.packaging_smoke",
        *pyopenms_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DirectInfusionQuant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    contents_directory=".",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(project_root / "packaging" / "windows_version_info.txt"),
)

# pyOpenMS runs in a Qt-free executable because the OpenMS and Qt DLL sets can
# conflict in one Windows process.
worker_analysis = Analysis(
    [str(project_root / "packaging" / "pyopenms_worker_entry.py")],
    pathex=[str(source_root)],
    binaries=pyopenms_binaries,
    datas=pyopenms_datas,
    hiddenimports=pyopenms_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "matplotlib", "pytest", "statsmodels", "tests"],
    noarchive=False,
    optimize=0,
)
worker_pyz = PYZ(worker_analysis.pure)
worker_exe = EXE(
    worker_pyz,
    worker_analysis.scripts,
    worker_analysis.binaries,
    worker_analysis.datas,
    [],
    name="DirectInfusionQuantPyOpenMSWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    worker_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DirectInfusionQuant",
)
