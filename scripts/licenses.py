"""Collect complete wheel/source license notices and publish corresponding sources."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vendor import ROOT, SOURCES, source_archive  # noqa: E402


def is_notice(path: Path) -> bool:
    return any(word in str(path).lower() for word in ("license", "copying", "notice", "copyright"))


def collect_licenses(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    names = [
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
        "pyinstaller",
        "packaging",
    ]
    versions = {name: importlib.metadata.version(name) for name in names}
    versions["Python"] = sys.version.split()[0]
    for name in names:
        package = importlib.metadata.distribution(name)
        for filename in package.files or ():
            if is_notice(filename) and filename.suffix not in {".py", ".pyc", ".so", ".dll"}:
                source = Path(package.locate_file(filename))
                if source.is_file():
                    target = destination / name / str(filename).replace("..", "_")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    for item in SOURCES:
        source = source_archive(item["name"])
        attributions = []
        with tarfile.open(source) as archive:
            for member in archive:
                path = Path(member.name)
                if not member.isfile() or path.is_absolute() or ".." in path.parts:
                    continue
                if (
                    is_notice(path)
                    and member.size < 5_000_000
                    and path.suffix.lower() not in {".py", ".cpp", ".h", ".png", ".svg", ".json"}
                ):
                    target = destination / item["name"] / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
                elif path.name == "qt_attribution.json":
                    entries = json.loads(archive.extractfile(member).read(), strict=False)
                    for entry in entries if isinstance(entries, list) else [entries]:
                        attributions.append(
                            "\n".join(f"{key}: {value}" for key, value in entry.items())
                        )
        if attributions:
            (destination / item["name"] / "THIRD_PARTY_ATTRIBUTIONS.txt").write_text(
                "\n\n".join(attributions), encoding="utf-8"
            )
    for filename in ("THIRD_PARTY_NOTICES.txt", "REBUILDING.txt"):
        shutil.copyfile(ROOT / "packaging" / filename, destination / filename)
    shutil.copytree(ROOT / "packaging/licenses", destination, dirs_exist_ok=True)
    (destination / "versions.txt").write_text(
        "\n".join(f"{name}: {version}" for name, version in versions.items()) + "\n",
        encoding="utf-8",
    )
    (destination / "SOURCE_INDEX.txt").write_text(
        "\n\n".join(
            f"{item['name']} {item['version']}\n{item['url']}\nSHA-256: {item['sha256']}"
            for item in SOURCES
        )
        + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    destination = ROOT / "build/third-party-licenses"
    collect_licenses(destination)
    if args.source_archive:
        args.source_archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.source_archive, "w", compression=zipfile.ZIP_STORED) as output:
            for item in SOURCES:
                archive = source_archive(item["name"])
                output.write(archive, "sources/" + archive.name)
            for filename in ("SOURCE_INDEX.txt", "REBUILDING.txt", "THIRD_PARTY_NOTICES.txt"):
                output.write(destination / filename, filename)
            for path in [
                *ROOT.glob("scripts/*.py"),
                *ROOT.glob("packaging/*.toml"),
                *ROOT.glob("packaging/*.spec"),
                ROOT / "requirements-build.txt",
            ]:
                output.write(path, str(path.relative_to(ROOT)))
        with args.source_archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        args.source_archive.with_suffix(".zip.sha256").write_text(
            f"{digest}  {args.source_archive.name}\n"
        )
        print(f"Corresponding sources: {args.source_archive.name}")
    print("License collection complete")


if __name__ == "__main__":
    main()
