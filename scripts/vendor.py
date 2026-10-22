"""Verified upstream sources shared by codec builds and license packaging."""

from __future__ import annotations

import hashlib
import tarfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = tomllib.loads((ROOT / "packaging/third-party-sources.toml").read_text())["source"]


def source_archive(name: str) -> Path:
    item = next(item for item in SOURCES if item["name"] == name)
    target = ROOT / "build/vendor-sources" / item["url"].rsplit("/", 1)[-1]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".part")
        with (
            urllib.request.urlopen(item["url"], timeout=120) as remote,
            temporary.open("wb") as out,
        ):
            while block := remote.read(1024 * 1024):
                out.write(block)
        temporary.replace(target)
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != item["sha256"]:
        raise RuntimeError(f"Source checksum mismatch: {name}")
    return target


def extract_source(name: str, destination: Path) -> Path:
    with tarfile.open(source_archive(name)) as archive:
        archive.extractall(destination, filter="data")
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"Expected one source root for {name}")
    return roots[0]
