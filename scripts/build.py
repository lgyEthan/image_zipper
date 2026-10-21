"""Build on the target OS; macOS bundles preserve symlinks inside a zip archive."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def collect_licenses(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    package_names = [
        "Pillow",
        "pillow-heif",
        "pikepdf",
        "pypdfium2",
        "img2pdf",
        "PySide6-Essentials",
        "shiboken6",
        "pyoxipng",
        "pyjpegli",
        "lxml",
    ]
    versions = {}
    for package_name in package_names:
        package = importlib.metadata.distribution(package_name)
        versions[package_name] = package.version
        for filename in package.files or ():
            if any(word in filename.name.lower() for word in ("license", "copying", "notice")):
                source = Path(package.locate_file(filename))
                if source.is_file():
                    target = destination / package_name / str(filename).replace("..", "_")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    (destination / "versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")


def main() -> None:
    os.chdir(ROOT)
    licenses = ROOT / "build" / "third-party-licenses"
    collect_licenses(licenses)
    env = dict(os.environ)
    env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-cache")
    command = [
        sys.executable,
        "-I",
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "ImageZipper",
        "--paths",
        str(ROOT / "src"),
        "--specpath",
        str(ROOT / "build"),
        "--collect-all",
        "pillow_heif",
        "--collect-all",
        "pypdfium2",
        "--collect-all",
        "pypdfium2_raw",
        "--hidden-import",
        "pyjpegli",
        "--hidden-import",
        "oxipng",
        "--collect-submodules",
        "PIL",
        "--copy-metadata",
        "pikepdf",
        "--add-data",
        f"{ROOT / 'src' / 'image_zipper' / 'assets'}:image_zipper/assets",
        "--add-data",
        f"{licenses}:third-party-licenses",
    ]
    from PySide6.QtWidgets import QApplication

    from image_zipper.app import app_icon

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Image Zipper build")
    icon = ROOT / "build" / "app-icon.png"
    app_icon().pixmap(256, 256).save(str(icon))
    command += ["--icon", str(icon)]
    if sys.platform == "darwin":
        command += ["--osx-bundle-identifier", "com.lgyethan.imagezipper"]
    command.append(str(ROOT / "packaging" / "launcher.py"))
    subprocess.run(command, check=True, env=env)
    binary = (
        ROOT
        / "dist"
        / (
            "ImageZipper.app/Contents/MacOS/ImageZipper"
            if sys.platform == "darwin"
            else "ImageZipper/ImageZipper.exe"
            if sys.platform == "win32"
            else "ImageZipper/ImageZipper"
        )
    )
    report = ROOT / "dist" / "self-test.json"
    check_env = dict(env, QT_QPA_PLATFORM="offscreen")
    for _ in range(3):
        completed = subprocess.run(
            [str(binary), "--self-test", str(report)], env=check_env, check=False, timeout=180
        )
        if completed.returncode:
            if report.exists():
                print(report.read_text(), flush=True)
            completed.check_returncode()
        if json.loads(report.read_text())["status"] != "passed":
            raise RuntimeError("Bundled application failed its smoke check")
    results = json.loads(report.read_text())
    results["verified_launches"] = 3
    report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    name = f"ImageZipper-{platform.system()}-{platform.machine()}"
    if sys.platform == "darwin":
        subprocess.run(
            [
                "ditto",
                "-c",
                "-k",
                "--sequesterRsrc",
                "--keepParent",
                "dist/ImageZipper.app",
                f"dist/{name}.zip",
            ],
            check=True,
        )
    else:
        shutil.make_archive(str(ROOT / "dist" / name), "zip", ROOT / "dist", "ImageZipper")
    print(f"Built and verified: dist/{name}.zip")


if __name__ == "__main__":
    main()
