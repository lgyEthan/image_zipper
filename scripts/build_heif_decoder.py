"""Build current HEIC decoding libraries without GPL encoders, then install a relocatable wheel."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vendor import ROOT, extract_source  # noqa: E402


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main():
    work = ROOT / "build/heif-decoder"
    work.mkdir(parents=True, exist_ok=True)
    prefix = work / "prefix"
    cmake = Path(sys.executable).parent / ("cmake.exe" if sys.platform == "win32" else "cmake")
    if not cmake.exists():
        raise RuntimeError("Install the codec build tools from requirements-build.txt first")
    common = [
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        f"-DCMAKE_PREFIX_PATH={prefix}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=ON",
        "-DCMAKE_INSTALL_LIBDIR=lib",
    ]
    if sys.platform == "darwin":
        common += ["-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0", f"-DCMAKE_INSTALL_NAME_DIR={prefix}/lib"]
    for name in ("libde265", "libheif"):
        source = extract_source(name, work / name / "source")
        options = ["-DENABLE_DECODER=OFF", "-DENABLE_ENCODER=OFF", "-DENABLE_SDL=OFF"]
        if name == "libheif":
            # Disable every optional codec before enabling only the HEVC decoder.
            # Enumerating the upstream options also prevents accidentally picking
            # up a host's GPL encoder or its plugins during a future source update.
            content = (source / "CMakeLists.txt").read_text()
            codecs = re.findall(r"^plugin_option\((\w+)\s", content, re.MULTILINE)
            options = [f"-DWITH_{codec}=OFF" for codec in codecs]
            options += [
                "-DWITH_LIBDE265=ON",
                "-DWITH_LIBDE265_PLUGIN=OFF",
                "-DENABLE_PLUGIN_LOADING=OFF",
                "-DWITH_LIBSHARPYUV=OFF",
                "-DWITH_HEADER_COMPRESSION=OFF",
                "-DWITH_EXAMPLES=OFF",
                "-DWITH_GDK_PIXBUF=OFF",
                "-DBUILD_TESTING=OFF",
                "-DBUILD_DOCUMENTATION=OFF",
                "-DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON",
                "-DCMAKE_DISABLE_FIND_PACKAGE_PNG=ON",
                "-DCMAKE_DISABLE_FIND_PACKAGE_TIFF=ON",
                "-DCMAKE_DISABLE_FIND_PACKAGE_WEBP=ON",
            ]
        binary = work / name / "cmake"
        run(cmake, "-S", source, "-B", binary, *common, *options)
        run(
            cmake,
            "--build",
            binary,
            "--config",
            "Release",
            "--parallel",
            min(os.cpu_count() or 2, 8),
        )
        run(cmake, "--install", binary, "--config", "Release")
    source = extract_source("pillow-heif", work / "pillow-heif/source")
    # setup.py may otherwise reuse objects compiled with an earlier configuration.
    if (source / "build").exists():
        shutil.rmtree(source / "build")
    if sys.platform == "win32":
        # Upstream assumes MSYS2 on Windows. Our libraries use MSVC, like CPython;
        # compile the unmodified C binding against that same toolchain instead.
        setup = source / "setup.py"
        content = setup.read_text()
        start = content.index('        if sys.platform.lower() == "win32":')
        end = content.index('        elif sys.platform.lower() == "darwin":', start)
        replacement = """        if sys.platform.lower() == "win32":
            self._update_extension("_pillow_heif", ["heif"])
"""
        setup.write_text(content[:start] + replacement + content[end:])
    (source / "LICENSES_bundled.txt").write_text(
        "Pictumi decoder-only build of pillow-heif 1.8.1 (BSD-3-Clause).\n"
        "Dynamic libraries: libheif 1.23.6 and libde265 1.1.3 (LGPL-3.0-or-later).\n"
        "No x265, x264, or other HEVC encoder is included.\n"
        "Complete sources, license texts and build instructions accompany Pictumi.\n"
    )
    wheels = work / "wheels"
    repaired = work / "repaired"
    for directory in (wheels, repaired):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir()
    env = dict(os.environ, LIBHEIF_ROOT=str(prefix), MACOSX_DEPLOYMENT_TARGET="15.0")
    if sys.platform == "darwin":
        env["CFLAGS"] = " ".join(
            [
                env.get("CFLAGS", ""),
                "-g0",
                shlex.quote(f"-ffile-prefix-map={ROOT}=."),
                shlex.quote(f"-fdebug-prefix-map={ROOT}=."),
            ]
        )
    elif sys.platform == "win32":
        env["CL"] = env.get("CL", "") + f' /pathmap:"{ROOT}=."'
        env["LINK"] = env.get("LINK", "") + " /PDBALTPATH:%_PDB%"
    run(sys.executable, "-I", "-m", "pip", "wheel", "--no-deps", "-w", wheels, source, env=env)
    wheel = next(wheels.glob("*.whl"))
    if sys.platform == "darwin":
        run(Path(sys.executable).parent / "delocate-wheel", "-w", repaired, wheel)
    elif sys.platform == "win32":
        run(
            sys.executable,
            "-I",
            "-m",
            "delvewheel",
            "repair",
            "--add-path",
            prefix / "bin",
            "-w",
            repaired,
            wheel,
        )
    else:
        raise RuntimeError("Release codec builds currently target macOS and Windows")
    run(
        sys.executable,
        "-I",
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--force-reinstall",
        next(repaired.glob("*.whl")),
    )
    run(
        sys.executable,
        "-I",
        "-c",
        "import pillow_heif; info=pillow_heif.libheif_info(); print(info); assert info['libheif']=='1.23.6'; assert not any(x in str(info['encoders']).lower() for x in ('x265', 'x264')); assert info['decoders']",
    )


if __name__ == "__main__":
    main()
