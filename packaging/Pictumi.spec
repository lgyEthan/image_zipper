"""Shared desktop bundle, including native OS version metadata."""

import os
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
icon = str(root / "build" / "app-icon.png")
datas = [
    (str(root / "src" / "pictumi" / "assets"), "pictumi/assets"),
    (str(root / "build" / "third-party-licenses"), "third-party-licenses"),
]
datas += copy_metadata("pikepdf")
binaries = []
hiddenimports = ["pyjpegli", "oxipng"] + collect_submodules("PIL")
for package in ("pillow_heif", "pypdfium2", "pypdfium2_raw"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

windows_version = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = tuple(int(part) for part in version.split(".")) + (0,)
    windows_version = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "lgyEthan"),
                            StringStruct("FileDescription", "Pictumi"),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", "Pictumi"),
                            StringStruct("OriginalFilename", "Pictumi.exe"),
                            StringStruct("ProductName", "Pictumi"),
                            StringStruct("ProductVersion", version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PIL.ImageTk"],
)
# pip's provenance for a locally rebuilt wheel contains the build-machine path.
# Runtime version and license metadata are retained; local install URLs are not.
a.datas = [entry for entry in a.datas if Path(entry[0]).name != "direct_url.json"]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Pictumi",
    console=False,
    icon=icon,
    version=windows_version,
    codesign_identity=(
        os.environ.get("PICTUMI_CODESIGN_IDENTITY") if sys.platform == "darwin" else None
    ),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Pictumi")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Pictumi.app",
        icon=icon,
        bundle_identifier="com.lgyethan.pictumi",
        version=version,
        info_plist={"CFBundleVersion": version, "LSMinimumSystemVersion": "15.0"},
    )
