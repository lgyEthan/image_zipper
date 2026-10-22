"""Check tracked source/history and release bundles without printing secret values."""

from __future__ import annotations

import argparse
import re
import subprocess
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "personal home path": re.compile(
        rb"(?:/(?:Users|home)/[A-Za-z0-9_.-]+/|[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+[\\/])"
    ),
    "private key": re.compile(
        rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]{64,}"
        rb"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
    ),
    "GitHub credential": re.compile(
        rb"\bgh[opusr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"
    ),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "API credential": re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{40,}\b"),
    "literal credential": re.compile(
        rb"""(?i)\b(?:password|api_key|secret_key|access_token)\s*[:=]\s*["'][A-Za-z0-9+/=_!@#$%-]{8,}["']"""
    ),
}


def findings(data: bytes) -> list[str]:
    return [name for name, pattern in PATTERNS.items() if pattern.search(data)]


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def check_source(history: bool = False) -> list[str]:
    problems = []
    paths = git("ls-files", "-z").decode().split("\0")
    for name in filter(None, paths):
        path = ROOT / name
        if path.suffix in {".p8", ".p12", ".pfx", ".key"} or path.name == ".env":
            problems.append(f"tracked credential file: {name}")
        if path.is_file():
            problems.extend(f"{name}: {kind}" for kind in findings(path.read_bytes()))
    if history:
        # Batch all objects once. Only blob payloads are checked; public author
        # names and certificate identities are explicitly allowed by the owner.
        objects = git("rev-list", "--objects", "--all").splitlines()
        ids = [line.split(b" ", 1)[0] for line in objects]
        process = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=ROOT,
            input=b"\n".join(ids) + b"\n",
            capture_output=True,
            check=True,
        )
        data, offset = process.stdout, 0
        while offset < len(data):
            end = data.index(b"\n", offset)
            oid, kind, size = data[offset:end].split()
            start, count = end + 1, int(size)
            if kind == b"blob":
                problems.extend(
                    f"history blob {oid[:12].decode()}: {finding}"
                    for finding in findings(data[start : start + count])
                )
            offset = start + count + 1
    return problems


def check_bundle(root: Path) -> list[str]:
    from PyInstaller.archive.readers import CArchiveReader

    problems = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        data = path.read_bytes()
        # Library binaries legitimately contain parser strings for PEM headers;
        # only complete key blocks with a payload count as private keys.
        for kind in findings(data):
            if kind == "personal home path" and ({"Frameworks", "_internal"} & set(relative.parts)):
                # Official Qt and wheel binaries carry their public CI build roots.
                build_users = {b"qt", b"runner", b"runneradmin"}
                if {"Python.framework", "python3__dot__12"} & set(relative.parts):
                    # The official python.org macOS framework embeds this build
                    # account in its native modules, on both architectures.
                    build_users.add(b"sysadmin")
                matches = PATTERNS[kind].findall(data)
                if all(
                    re.split(rb"[\\/]+", match.rstrip(b"/\\"))[-1] in build_users
                    for match in matches
                ):
                    continue
            problems.append(f"bundle {relative}: {kind}")
        if path.name not in {"Pictumi", "Pictumi.exe"}:
            continue
        archive = CArchiveReader(str(path))
        for name in archive.toc:
            if not name.endswith(".pyz"):
                continue
            pyz = archive.open_embedded_archive(name)

            def inspect_code(code, module):
                if not isinstance(code, types.CodeType):
                    return
                if findings(code.co_filename.encode()):
                    problems.append(f"bundle code {module}: personal source path")
                for value in code.co_consts:
                    if isinstance(value, types.CodeType):
                        inspect_code(value, module)
                    elif isinstance(value, (str, bytes)):
                        raw = value.encode() if isinstance(value, str) else value
                        problems.extend(f"bundle code {module}: {kind}" for kind in findings(raw))

            for module in pyz.toc:
                if module.startswith("pictumi"):
                    inspect_code(pyz.extract(module), module)
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    problems = check_bundle(args.bundle) if args.bundle else check_source(args.history)
    for problem in sorted(set(problems)):
        print(problem)
    print(f"Privacy check: {'FAILED' if problems else 'passed'}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
