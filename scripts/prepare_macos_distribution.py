"""Prepare a verified app for Xcode's Developer ID distribution workflow."""

import argparse
import datetime
import plistlib
import re
import subprocess
import sys
from pathlib import Path


def run(*args):
    completed = subprocess.run(args, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def is_macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as file:
        return file.read(4) in {
            b"\xfe\xed\xfa\xce",
            b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
            b"\xca\xfe\xba\xbf",
            b"\xbf\xba\xfe\xca",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument(
        "--resign",
        action="store_true",
        help="Sign a copy of an existing CI app, including every native library",
    )
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Developer ID distribution requires macOS")
    app = args.app.resolve()
    archive = args.archive.resolve()
    if not app.is_dir() or app.suffix != ".app":
        parser.error("--app must be an existing application bundle")
    if archive.suffix != ".xcarchive":
        parser.error("--archive must end in .xcarchive")
    if archive.exists() or archive.with_suffix(".export-options.plist").exists():
        parser.error("Archive or export options already exist; choose a new output path")
    identities = run("security", "find-identity", "-v", "-p", "codesigning").stdout
    matches = [
        (fingerprint, name)
        for fingerprint, name in re.findall(r'([0-9A-Fa-f]{40}) "([^"]+)"', identities)
        if name.startswith("Developer ID Application: ") and args.identity in {fingerprint, name}
    ]
    if len(matches) != 1:
        parser.error("Choose one valid Developer ID Application identity")
    fingerprint, name = matches[0]
    team_match = re.search(r"\(([A-Z0-9]{10})\)$", name)
    if team_match is None:
        raise RuntimeError("Could not determine the Developer ID team")
    team = team_match.group(1)
    with (app / "Contents/Info.plist").open("rb") as file:
        metadata = plistlib.load(file)
    if metadata["CFBundleIdentifier"] != "com.lgyethan.imagezipper":
        parser.error("Only Image Zipper application bundles are supported")
    binary = app / "Contents/MacOS" / metadata["CFBundleExecutable"]
    architecture = run("lipo", "-archs", str(binary)).stdout.strip().split()
    macho = sorted(
        (path for path in app.rglob("*") if is_macho(path)),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    if args.resign:
        frameworks = sorted(
            (path for path in app.rglob("*.framework") if path.is_dir() and not path.is_symlink()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        targets = macho + frameworks + [app]
        for index, path in enumerate(targets, 1):
            print(
                f"Signing {index}/{len(targets)}: {path.relative_to(app) if path != app else app.name}",
                flush=True,
            )
            run(
                "codesign",
                "--force",
                "--sign",
                fingerprint,
                "--all-architectures",
                "--timestamp",
                "--options",
                "runtime",
                str(path),
            )
    for path in [*macho, app]:
        signature = run("codesign", "--display", "--verbose=4", str(path)).stderr
        if not all(
            value in signature
            for value in (f"Authority={name}", f"TeamIdentifier={team}", "Timestamp=", "(runtime)")
        ):
            raise RuntimeError(
                f"Missing Developer ID, team, timestamp, or hardened runtime: {path}"
            )
    run("codesign", "--verify", "--deep", "--strict", str(app))
    destination = archive / "Products/Applications" / app.name
    destination.parent.mkdir(parents=True)
    run("ditto", str(app), str(destination))
    properties = {
        "ApplicationPath": f"Applications/{app.name}",
        "Architectures": architecture,
        "CFBundleIdentifier": metadata["CFBundleIdentifier"],
        "CFBundleShortVersionString": metadata["CFBundleShortVersionString"],
        "CFBundleVersion": metadata["CFBundleVersion"],
        "SigningIdentity": name,
        "Team": team,
    }
    with (archive / "Info.plist").open("wb") as file:
        plistlib.dump(
            {
                "ApplicationProperties": properties,
                "ArchiveVersion": 2,
                "CreationDate": datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
                "Name": "Image Zipper",
                "SchemeName": "ImageZipper",
            },
            file,
        )
    options = archive.with_suffix(".export-options.plist")
    with options.open("wb") as file:
        plistlib.dump(
            {
                "method": "developer-id",
                "destination": "upload",
                "signingStyle": "manual",
                "signingCertificate": fingerprint,
                "teamID": team,
                "manageAppVersionAndBuildNumber": False,
                "stripSwiftSymbols": False,
            },
            file,
        )
    run("codesign", "--verify", "--deep", "--strict", str(destination))
    print(f"Verified {len(macho)} Mach-O files, archive: {archive}")
    print(f"Export options: {options}")


if __name__ == "__main__":
    main()
