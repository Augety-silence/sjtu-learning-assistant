#!/usr/bin/env python3
"""Native, offline gates for source-built FFmpeg stages and frozen app bundles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import struct
import subprocess
import sys
import tarfile
import tempfile
import wave
from pathlib import Path

SOURCE_COMMIT = "f46e514491172d15bd74b4abb1814cd2f05a763e"
SOURCE_SHA256 = "1fa39b5a6ae9ac02c2cf280ec5cc8321a0dd0b9ab34b6c73133cafccaf5dfa79"
SOURCE_URL = f"https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/{SOURCE_COMMIT}"
VERSION = "7.1.3"
REQUIRED_FLAGS = {
    "--disable-gpl", "--disable-nonfree", "--disable-version3",
    "--disable-autodetect", "--disable-shared", "--enable-static",
    "--disable-network", "--disable-debug", "--disable-doc",
    "--disable-ffplay", "--disable-ffprobe", "--disable-protocols",
    "--enable-protocol=file,pipe", "--pkg-config=false",
}
SYSTEM_DLLS = {
    "advapi32.dll", "avrt.dll", "bcrypt.dll", "comdlg32.dll", "crypt32.dll",
    "dwmapi.dll", "gdi32.dll", "imm32.dll", "kernel32.dll", "kernelbase.dll",
    "mf.dll", "mfplat.dll", "mfreadwrite.dll", "mfuuid.dll", "msvcrt.dll",
    "ntdll.dll", "ole32.dll", "oleaut32.dll", "psapi.dll", "rpcrt4.dll",
    "secur32.dll", "setupapi.dll", "shell32.dll", "shlwapi.dll", "ucrtbase.dll",
    "user32.dll", "version.dll", "vfw32.dll", "winmm.dll", "ws2_32.dll",
}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(executable: Path, *args: str) -> str:
    # Prevent environment-selected DLLs/libraries from hiding missing dependencies.
    env = os.environ.copy()
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        env.pop(key, None)
    if sys.platform == "win32":
        env["PATH"] = str(Path(env.get("SystemRoot", "C:/Windows")) / "System32")
    result = subprocess.run(
        [str(executable), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, env=env,
    )
    require(result.returncode == 0, f"FFmpeg command failed: {' '.join(args[:2])}\n{result.stderr[-1500:]}")
    return result.stdout + result.stderr


def pe_imports(path: Path) -> set[str]:
    """Read normal and delay PE imports without requiring objdump/VS at runtime."""
    data = path.read_bytes()
    require(data[:2] == b"MZ", "Expected a native Windows PE executable")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    require(data[pe:pe + 4] == b"PE\0\0", "Invalid PE header")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    require(magic == 0x20B, "FFmpeg Windows executable must be x64 PE32+")
    require(struct.unpack_from("<H", data, pe + 4)[0] == 0x8664, "Expected AMD64 FFmpeg")
    directory = optional + 112
    image_base = struct.unpack_from("<Q", data, optional + 24)[0]
    sections = []
    for i in range(count):
        offset = optional + optional_size + i * 40
        virtual_size, address, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        sections.append((address, max(virtual_size, raw_size), raw_offset))

    def file_offset(rva: int) -> int:
        for address, size, raw in sections:
            if address <= rva < address + size:
                return raw + rva - address
        raise VerificationError("PE import address outside sections")

    def dll_name(rva: int) -> str:
        offset = file_offset(rva)
        end = data.find(b"\0", offset, offset + 256)
        require(end > offset, "Invalid PE DLL import name")
        return data[offset:end].decode("ascii").lower()

    imports: set[str] = set()
    directory_count = struct.unpack_from("<I", data, optional + 108)[0]
    for index, stride in ((1, 20), (13, 32)):
        if directory_count <= index:
            continue
        rva, size = struct.unpack_from("<II", data, directory + index * 8)
        if not rva:
            continue
        offset = file_offset(rva)
        for item in range(min(size // stride + 1, 4096)):
            fields = struct.unpack_from("<" + "I" * (stride // 4), data, offset + item * stride)
            if not any(fields):
                break
            if index == 1:
                name_rva = fields[3]
            else:
                name_rva = fields[1] if fields[0] & 1 else fields[1] - image_base
            imports.add(dll_name(name_rva))
        else:
            raise VerificationError("Unterminated PE import table")
    require(bool(imports), "PE dependency inspection found no imports")
    return imports


def verify_dependencies(executable: Path) -> list[str]:
    if sys.platform == "darwin":
        output = subprocess.run(
            ["/usr/bin/otool", "-L", str(executable)], check=True,
            capture_output=True, text=True, timeout=30,
        ).stdout
        dependencies = [line.strip().split(" (", 1)[0] for line in output.splitlines()[1:] if line.strip()]
        require(bool(dependencies), "Mach-O dependency inspection found no libraries")
        require(all(dep.startswith(("/usr/lib/", "/System/Library/")) for dep in dependencies),
                "FFmpeg depends on a non-system macOS library")
        return dependencies
    if sys.platform == "win32":
        dependencies = pe_imports(executable)
        require(all(dep in SYSTEM_DLLS or dep.startswith(("api-ms-win-", "ext-ms-win-")) for dep in dependencies),
                "FFmpeg imports non-system DLLs: " + ", ".join(sorted(dependencies - SYSTEM_DLLS)))
        return sorted(dependencies)
    raise VerificationError("Run native verification on macOS or Windows, not a cross-build host")


def verify_license_and_flags(license_output: str, buildconf: str, version_output: str) -> list[str]:
    require(re.search(r"ffmpeg version 7\.1\.3(?:\s|$)", version_output) is not None,
            "Expected official FFmpeg version 7.1.3")
    normalized_license = " ".join(license_output.partition("ffmpeg version").__getitem__(0).split())
    require("GNU Lesser General Public License" in normalized_license and "version 2.1" in normalized_license,
            "FFmpeg -L does not report LGPL 2.1")
    require("GNU General Public License" not in normalized_license and "nonfree" not in normalized_license.lower(),
            "FFmpeg reports GPL or nonfree licensing")
    _, separator, configuration = buildconf.partition("configuration:")
    require(bool(separator), "Missing FFmpeg build configuration")
    flags = shlex.split(configuration.strip())
    require(REQUIRED_FLAGS <= set(flags), "Missing required LGPL/offline/static build flags: " + ", ".join(sorted(REQUIRED_FLAGS - set(flags))))
    forbidden = (
        "--enable-gpl", "--enable-nonfree", "--enable-version3", "--enable-shared",
        "--enable-network", "--enable-autodetect", "--enable-lib", "--disable-everything",
        "--disable-decoder", "--disable-encoder", "--disable-demuxer", "--disable-muxer",
        "--disable-filter", "--disable-swscale", "--disable-swresample",
    )
    require(not any(flag.startswith(forbidden) for flag in flags), "Unexpected external library or disabled built-in capability")
    require(not re.search(r"/Users/|/home/|[A-Za-z]:[\\/]Users[\\/]", configuration),
            "Build configuration contains private paths")
    return flags


def names(output: str, *, aliases: bool = False) -> set[str]:
    found: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"^\s*[A-Z.]{1,6}\s+(\S+)\s", line)
        if match:
            found.update(match.group(1).split(",") if aliases else (match.group(1),))
    return found


def verify_capabilities(executable: Path) -> None:
    expected = {
        "-decoders": {"aac", "mp3", "flac", "opus", "vorbis", "h264", "hevc", "mpeg4", "mpeg2video", "vp8", "vp9", "wmv1", "wmv2", "wmv3", "vc1", "mjpeg"},
        "-encoders": {"aac", "mjpeg", "mpeg4", "pcm_s16le"},
        "-demuxers": {"mov", "matroska", "avi", "asf", "flv", "ogg", "mpeg", "mpegts", "wav"},
        "-muxers": {"mp4", "mov", "ipod", "wav", "image2", "matroska"},
        "-filters": {"fps", "scale", "aresample"},
    }
    for option, required in expected.items():
        available = names(run(executable, "-hide_banner", option), aliases=True)
        require(required <= available, f"Missing {option} capabilities: {', '.join(sorted(required - available))}")
    protocols = run(executable, "-hide_banner", "-protocols")
    protocols_found = {line.strip() for line in protocols.splitlines() if re.fullmatch(r"\s+[a-z0-9_]+\s*", line)}
    require(protocols_found == {"file", "pipe"}, "Only native file/pipe protocols may be enabled")


def smoke(executable: Path) -> None:
    """Synthetic video -> JPEG, native AAC encode/copy, audio resampling; no API."""
    with tempfile.TemporaryDirectory(prefix="sjtu-ffmpeg-smoke-") as directory:
        root = Path(directory)
        wav = root / "input.wav"
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b"\0\0" * 22050)
        common = ("-hide_banner", "-loglevel", "error", "-nostdin", "-y")
        aac = root / "audio.m4a"
        run(executable, *common, "-i", str(wav), "-c:a", "aac", str(aac))
        video = root / "video.mp4"
        # Raw frames avoid requiring external codecs or the lavfi input device.
        raw = root / "frames.rgb"
        raw.write_bytes(bytes((40, 100, 180)) * 64 * 48 * 4)
        run(executable, *common, "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "64x48",
            "-framerate", "8", "-i", str(raw), "-i", str(aac), "-c:v", "mpeg4", "-c:a", "copy", "-shortest", str(video))
        jpeg = root / "screenshot.jpg"
        run(executable, *common, "-i", str(video), "-vf", "fps=2,scale=32:24", "-frames:v", "1", "-c:v", "mjpeg", str(jpeg))
        require(jpeg.read_bytes().startswith(b"\xff\xd8"), "JPEG screenshot smoke failed")
        output = root / "resampled.wav"
        run(executable, *common, "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output))
        with wave.open(str(output), "rb") as audio:
            require(audio.getframerate() == 16000 and audio.getnframes() > 0, "AAC decode/resampling smoke failed")
        pipe = run(executable, "-hide_banner", "-loglevel", "error", "-i", str(wav), "-f", "null", "pipe:1")
        require(not pipe.strip(), "File/pipe smoke failed")


def verify_source(stage: Path) -> None:
    archive = stage / "source" / "FFmpeg-n7.1.3.tar.gz"
    require(archive.is_file() and archive.stat().st_size == 15926127 and sha256(archive) == SOURCE_SHA256,
            "Missing or modified pinned FFmpeg source archive")
    with tarfile.open(archive, "r:gz") as source:
        member = source.getmember(f"FFmpeg-{SOURCE_COMMIT}/COPYING.LGPLv2.1")
        stream = source.extractfile(member)
        require(stream is not None, "Source archive lacks LGPL license")
        require((stage / "licenses" / "COPYING.LGPLv2.1").read_bytes() == stream.read(),
                "Bundled LGPL 2.1 license differs from verbatim pinned source license")
    for filename in ("build_ffmpeg.sh", "verify_ffmpeg.py"):
        require((stage / "source" / filename).is_file(), f"Missing portable recipe: {filename}")


def locate_bundle(bundle: Path) -> Path:
    candidates = (bundle / "Contents" / "Frameworks" / "ffmpeg", bundle / "Contents" / "Resources" / "ffmpeg", bundle / "_internal" / "ffmpeg", bundle / "ffmpeg")
    for path in candidates:
        if (path / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")).is_file():
            return path
    raise VerificationError("Missing dedicated bundled ffmpeg/bin executable")


def verify_no_vendor_binaries(bundle: Path) -> None:
    for path in bundle.rglob("*"):
        parts = {part.lower() for part in path.parts}
        require(not (path.is_file() and "imageio_ffmpeg" in parts and "binaries" in parts),
                "Forbidden imageio_ffmpeg/binaries vendor data in bundle")
        if path.is_file() and path.name.lower().startswith("ffmpeg") and path.suffix.lower() in {"", ".exe"}:
            require(path.parent.name == "bin" and path.parent.parent.name == "ffmpeg", "Unexpected FFmpeg executable outside dedicated bundle path")


def verify(stage: Path, *, write_manifest: bool = False, packaged: bool = False) -> dict:
    executable = stage / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    require(executable.is_file() and os.access(executable, os.X_OK), "Missing executable in ffmpeg/bin")
    verify_source(stage)
    version_output = run(executable, "-version")
    flags = verify_license_and_flags(run(executable, "-L"), run(executable, "-hide_banner", "-buildconf"), version_output)
    if sys.platform == "win32":
        require("--disable-x86asm" in flags, "Windows build requires --disable-x86asm")
    dependencies = verify_dependencies(executable)
    verify_capabilities(executable)
    smoke(executable)
    compiler = next((line.removeprefix("built with ") for line in version_output.splitlines() if line.startswith("built with ")), "")
    require(bool(compiler), "Missing compiler identity")
    manifest = {
        "schema_version": 1, "version": VERSION, "source_tag": "n7.1.3",
        "source_commit": SOURCE_COMMIT, "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256, "source_bytes": 15926127,
        "license": "LGPL-2.1-or-later", "license_sha256": sha256(stage / "licenses" / "COPYING.LGPLv2.1"),
        "compiler": compiler, "buildconf": flags, "system_dependencies": dependencies,
        "binary": f"bin/{executable.name}", "binary_sha256": sha256(executable),
        "binary_hash_scope": "unmodified-stage-before-packager-signing",
        "recipe_sha256": {filename: sha256(stage / "source" / filename) for filename in ("build_ffmpeg.sh", "verify_ffmpeg.py")},
    }
    path = stage / "build-manifest.json"
    if write_manifest:
        require(not packaged, "Cannot rewrite a packaged manifest")
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        stored = json.loads(path.read_text(encoding="utf-8"))
        for key, value in manifest.items():
            # PyInstaller/codesign can rewrite a Mach-O executable; the manifest
            # deliberately records the source-built hash, not a post-signing hash.
            if packaged and key == "binary_sha256":
                continue
            require(stored.get(key) == value, f"FFmpeg manifest mismatch: {key}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--stage", type=Path)
    target.add_argument("--bundle", type=Path)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.bundle:
            verify_no_vendor_binaries(args.bundle)
            stage = locate_bundle(args.bundle)
        else:
            stage = args.stage
            verify_no_vendor_binaries(stage)
        verify(stage, write_manifest=args.write_manifest, packaged=bool(args.bundle))
    except (VerificationError, OSError, ValueError, struct.error, tarfile.TarError, subprocess.SubprocessError) as exc:
        print(f"FFmpeg verification failed: {exc}", file=sys.stderr)
        return 1
    print("FFmpeg verified: pinned 7.1.3 source, LGPL 2.1, offline/static build, system-only dependencies, JPEG/AAC smoke.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
