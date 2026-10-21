"""Build on the target OS; macOS bundles preserve symlinks inside a zip archive."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tomllib
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codesign-identity",
        help="macOS Developer ID Application certificate's full name or SHA-1 fingerprint",
    )
    args = parser.parse_args()
    identity = None
    if args.codesign_identity:
        if sys.platform != "darwin":
            parser.error("Developer ID signing is only available on macOS")
        identities = subprocess.check_output(
            ["security", "find-identity", "-v", "-p", "codesigning"], text=True
        )
        matches = [
            fingerprint
            for fingerprint, name in re.findall(r'([0-9A-Fa-f]{40}) "([^"]+)"', identities)
            if name.startswith("Developer ID Application: ")
            and args.codesign_identity in {fingerprint, name}
        ]
        if len(matches) != 1:
            parser.error("Select one installed, valid Developer ID Application signing identity")
        identity = matches[0]
    os.chdir(ROOT)
    from image_zipper import __version__

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    if __version__ != version:
        raise RuntimeError("Application and project versions do not match")
    licenses = ROOT / "build" / "third-party-licenses"
    collect_licenses(licenses)
    env = dict(os.environ)
    env.pop("IMAGE_ZIPPER_CODESIGN_IDENTITY", None)
    if identity:
        env["IMAGE_ZIPPER_CODESIGN_IDENTITY"] = identity
    env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-cache")
    command = [
        sys.executable,
        "-I",
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(ROOT / "packaging" / "ImageZipper.spec"),
    ]
    from PySide6.QtWidgets import QApplication

    from image_zipper.app import app_icon

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Image Zipper build")
    icon = ROOT / "build" / "app-icon.png"
    app_icon().pixmap(256, 256).save(str(icon))
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
    if sys.platform == "darwin":
        with (binary.parent.parent / "Info.plist").open("rb") as file:
            metadata = plistlib.load(file)
        if (
            metadata.get("CFBundleShortVersionString") != version
            or metadata.get("CFBundleVersion") != version
        ):
            raise RuntimeError("Mac bundle version does not match the application")
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(binary.parent.parent.parent)],
            check=True,
        )
        if identity:
            signature = subprocess.run(
                ["codesign", "--display", "--verbose=4", str(binary.parent.parent.parent)],
                capture_output=True,
                text=True,
                check=True,
            ).stderr
            if (
                "Authority=Developer ID Application:" not in signature
                or "(runtime)" not in signature
            ):
                raise RuntimeError(
                    "The bundle requires a Developer ID signature and hardened runtime"
                )
    elif sys.platform == "win32":
        from PyInstaller.utils.win32.versioninfo import read_version_info_from_executable

        resource = read_version_info_from_executable(str(binary))
        major, minor, patch = (int(part) for part in version.split("."))
        if resource is None or (resource.ffi.fileVersionMS, resource.ffi.fileVersionLS) != (
            (major << 16) | minor,
            patch << 16,
        ):
            raise RuntimeError("Windows executable version does not match the application")
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
        result = json.loads(report.read_text())
        if result["status"] != "passed" or result.get("version") != version:
            raise RuntimeError("Bundled application failed its smoke check")
    results = json.loads(report.read_text())
    results["verified_launches"] = 3
    report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    system = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, platform.system())
    machine = {"AMD64": "x64", "x86_64": "Intel" if sys.platform == "darwin" else "x64"}.get(
        platform.machine(), platform.machine()
    )
    name = f"ImageZipper-v{version}-{system}-{machine}"
    if identity:
        # A valid signature alone is not Apple notarization.
        name += "-signed"
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
    archive = ROOT / "dist" / f"{name}.zip"
    with archive.open("rb") as file:
        checksum = hashlib.file_digest(file, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    print(f"Built and verified: dist/{name}.zip")


if __name__ == "__main__":
    main()
