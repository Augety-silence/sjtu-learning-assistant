#!/usr/bin/env python3
"""Fail-closed cross-host release validation and draft-only GitHub asset handling.

Native JSON is authorized by GitHub's authenticated asset uploader, not by a
self-declared actor inside JSON. Ubuntu validates portable bytes/PE imports but
never claims to execute Windows FFmpeg or inspect a DMG natively.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import shlex
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import verify_ffmpeg as ffmpeg
import prepare_release_evidence as proof

HEX = re.compile(r"[0-9a-f]{64}")
BOT = "github-actions[bot]"
MAX_JSON = 256 * 1024


def require(value, message):
    proof.require(value, message)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    require(path.stat().st_size <= MAX_JSON, "Evidence/manifest is unexpectedly large")
    result = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(result, dict), "Expected JSON object")
    return result


def required_names(version):
    primary = (*proof.artifact_names(version)["macos"], *proof.artifact_names(version)["windows"],
               *proof.SHARED_NAMES, "mac-evidence.json", "windows-evidence.json")
    return {name for base in primary for name in (base, base + ".sha256")}


def checksum(path):
    sidecar = path.with_name(path.name + ".sha256")
    require(sidecar.stat().st_size < 1024, "Invalid checksum size")
    require(sidecar.read_text(encoding="ascii") == f"{proof.digest(path)}  {path.name}\n", "Checksum/name mismatch: " + path.name)


def validate_manifest(manifest, platform_name, code):
    expected = {
        "schema_version": 1, "version": "7.1.3", "source_tag": "n7.1.3",
        "source_commit": ffmpeg.SOURCE_COMMIT, "source_url": ffmpeg.SOURCE_URL,
        "source_sha256": ffmpeg.SOURCE_SHA256, "source_bytes": 15926127,
        "license": "LGPL-2.1-or-later", "binary_hash_scope": "unmodified-stage-before-packager-signing",
        "binary": "bin/ffmpeg.exe" if platform_name == "windows" else "bin/ffmpeg",
        "recipe_sha256": {name: code["scripts/" + name] for name in ("build_ffmpeg.sh", "verify_ffmpeg.py")},
    }
    require(all(manifest.get(key) == value for key, value in expected.items()), "Pinned source/recipe/license manifest mismatch")
    for key in ("binary_sha256", "license_sha256"):
        require(isinstance(manifest.get(key), str) and HEX.fullmatch(manifest[key]), "Missing manifest hash")
    require(isinstance(manifest.get("compiler"), str) and manifest["compiler"], "Missing native compiler")
    flags = manifest.get("buildconf")
    require(isinstance(flags, list) and all(isinstance(flag, str) for flag in flags), "Missing native buildconf")
    ffmpeg.verify_license_and_flags("GNU Lesser General Public License version 2.1", "configuration: " + shlex.join(flags), "ffmpeg version 7.1.3\n")
    deps = manifest.get("system_dependencies")
    require(isinstance(deps, list) and deps and all(isinstance(dep, str) for dep in deps), "Missing native dependencies")
    if platform_name == "windows":
        require("--disable-x86asm" in flags, "Windows x86asm gate missing")
        require(all(dep in ffmpeg.SYSTEM_DLLS or dep.startswith(("api-ms-win-", "ext-ms-win-")) for dep in deps), "Non-system Windows dependency")
    else:
        require(all(dep.startswith(("/usr/lib/", "/System/Library/")) for dep in deps), "Non-system macOS dependency")


def validate_evidence(evidence, platform_name, tag, commit, version, code, directory):
    expected = {"schema_version": 1, "platform": platform_name, "architecture": "arm64" if platform_name == "macos" else "x64",
                "tag": tag, "app_version": version, "release_commit": commit, "code_sha256": code}
    require(all(evidence.get(k) == v for k, v in expected.items()), "Evidence identity/code/version mismatch")
    native = evidence["native_verification"]
    require(native.get("result") == "passed" and native.get("host_os") == ("darwin" if platform_name == "macos" else "win32"), "Missing native verification")
    checks = native.get("checks")
    require(isinstance(checks, list), "Missing executed native checks")
    needed = {"ffmpeg_stage": "python", "ffmpeg_bundle": "python"}
    if platform_name == "macos":
        needed.update({"macos_bundle": "python", "dmg_integrity": "hdiutil", "dmg_attach_readonly": "hdiutil",
                       "dmg_bundle": "python", "dmg_ffmpeg": "python", "dmg_detach": "hdiutil"})
    else:
        needed["windows_manifest"] = "python"
    require(len(checks) == len(needed), "Native check set mismatch")
    by_check = {item["check"]: item for item in checks}
    require(set(by_check) == set(needed), "Missing/duplicate executed native check")
    for name, tool in needed.items():
        item = by_check[name]
        require(type(item.get("returncode")) is int and item["returncode"] == 0, "Native command failed")
        require(item.get("tool") == "hdiutil" if tool == "hdiutil" else bool(re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", item.get("tool", ""))), "Unexpected proof command")
    fm = evidence["ffmpeg"]
    manifest = fm["stage_manifest"]
    validate_manifest(manifest, platform_name, code)
    require(fm["stage_binary_sha256"] == manifest["binary_sha256"], "Stage binary hash scope mismatch")
    for key in ("packaged_binary_sha256", "packaged_manifest_sha256"):
        require(isinstance(fm.get(key), str) and HEX.fullmatch(fm[key]), "Missing packaged FFmpeg hash")
    paths = ("Contents/Frameworks/ffmpeg/bin/ffmpeg", "Contents/Resources/ffmpeg/bin/ffmpeg") if platform_name == "macos" else ("_internal/ffmpeg/bin/ffmpeg.exe", "ffmpeg/bin/ffmpeg.exe")
    require(fm.get("packaged_path") in paths, "Unexpected FFmpeg package path")
    app = evidence["application"]
    require(HEX.fullmatch(app.get("sha256", "")), "Missing application hash")
    require(app.get("path") == ("Contents/MacOS/" + proof.APP if platform_name == "macos" else proof.APP + ".exe"), "Unexpected app executable path")
    if platform_name == "macos":
        plist = app["plist"]
        for key, value in {"CFBundleIdentifier": "io.github.sjtu-learning-assistant", "CFBundleName": proof.APP,
                           "CFBundleShortVersionString": version, "CFBundleVersion": version}.items():
            require(plist.get(key) == value, "Native plist proof mismatch")
        require(HEX.fullmatch(plist.get("sha256", "")) and HEX.fullmatch(app.get("bundle_tree_sha256", "")), "Missing native plist/tree digest")
        require(app.get("dmg_matches_bundle") is True, "DMG not compared to native bundle")
    else:
        require(app.get("manifest_verified") is True and app.get("version_scope") == "exact-commit-build", "Missing native Windows app proof")
        require(fm["packaged_binary_sha256"] == fm["stage_binary_sha256"], "Windows stage/package mismatch")
    entries = evidence["assets"]
    names = {*proof.artifact_names(version)[platform_name], *proof.SHARED_NAMES}
    require(isinstance(entries, list) and len(entries) == len(names), "Platform asset list mismatch")
    require({item["name"] for item in entries} == names, "Unexpected/missing artifact names")
    for item in entries:
        path = directory / item["name"]
        require(item["sha256"] == proof.digest(path) and type(item["bytes"]) is int and item["bytes"] == path.stat().st_size > 0, "Artifact differs from native proof: " + path.name)
    require(manifest["license_sha256"] == proof.digest(directory / "COPYING.LGPLv2.1"), "License proof mismatch")


def verify_installer(path):
    """Require a PE installer container, not a renamed/source-only file."""
    with path.open("rb") as stream:
        header = stream.read(64)
        require(len(header) == 64 and header[:2] == b"MZ", "Windows Setup is not a PE executable")
        offset = struct.unpack_from("<I", header, 0x3C)[0]
        require(64 <= offset <= min(path.stat().st_size - 26, 1024 * 1024), "Invalid installer PE offset")
        stream.seek(offset)
        pe = stream.read(26)
    require(pe[:4] == b"PE\0\0" and struct.unpack_from("<H", pe, 4)[0] in (0x14C, 0x8664)
            and struct.unpack_from("<H", pe, 24)[0] in (0x10B, 0x20B), "Invalid Windows Setup PE header")


def verify_windows_zip(path, evidence):
    fm = evidence["ffmpeg"]
    manifest = fm["stage_manifest"]
    root = str(PurePosixPath(fm["packaged_path"]).parent.parent)
    expected = {
        fm["packaged_path"]: fm["packaged_binary_sha256"],
        root + "/build-manifest.json": fm["packaged_manifest_sha256"],
        root + "/source/FFmpeg-n7.1.3.tar.gz": ffmpeg.SOURCE_SHA256,
        root + "/source/build_ffmpeg.sh": manifest["recipe_sha256"]["build_ffmpeg.sh"],
        root + "/source/verify_ffmpeg.py": manifest["recipe_sha256"]["verify_ffmpeg.py"],
        root + "/licenses/COPYING.LGPLv2.1": manifest["license_sha256"],
        evidence["application"]["path"]: evidence["application"]["sha256"],
    }
    with zipfile.ZipFile(path) as archive:
        seen = set()
        for item in archive.infolist():
            name = item.filename
            pure = PurePosixPath(name)
            require(name not in seen and "\\" not in name and not pure.is_absolute() and ".." not in pure.parts and ":" not in name, "Unsafe/duplicate ZIP member")
            seen.add(name)
            require((item.external_attr >> 16) & 0o170000 != 0o120000, "Unexpected Windows ZIP symlink")
            require(item.file_size <= 2 * 1024**3, "Oversized ZIP member")
            parts = {part.lower() for part in pure.parts}
            require(not ("imageio_ffmpeg" in parts and "binaries" in parts and not item.is_dir()), "Vendor FFmpeg leaked into portable ZIP")
            if pure.name.lower().startswith("ffmpeg") and pure.suffix.lower() in ("", ".exe") and not item.is_dir():
                require(name == fm["packaged_path"], "Unexpected FFmpeg executable in ZIP")
        for name, expected_hash in expected.items():
            require(name in seen, "Missing packaged proof member: " + name)
            h = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            require(h.hexdigest() == expected_hash, "Portable ZIP hash mismatch: " + name)
        info = archive.getinfo(root + "/build-manifest.json")
        require(info.file_size <= MAX_JSON and json.loads(archive.read(info)) == manifest, "Portable manifest differs from native proof")
        require(archive.getinfo(root + "/source/FFmpeg-n7.1.3.tar.gz").file_size == 15926127, "Portable source size mismatch")
        # Static import validation is additional proof, not native execution.
        with tempfile.TemporaryDirectory(prefix="sjtu-release-pe-") as directory:
            binary = Path(directory) / "ffmpeg.exe"
            with archive.open(fm["packaged_path"]) as src, binary.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            require(sorted(ffmpeg.pe_imports(binary)) == sorted(manifest["system_dependencies"]), "ZIP PE imports differ from native dependencies")


def verify_directory(directory, tag, commit, metadata, mac_uploader, local_mac=True):
    version, code = proof.validate_identity(tag, commit)
    names = required_names(version)
    require(metadata.get("draft") is True and metadata.get("tag_name") == tag, "Release must still be the exact draft")
    remote = metadata.get("assets", [])
    require(len(remote) == len(names) and {item["name"] for item in remote} == names, "Missing/extra/duplicate draft assets")
    require({p.name for p in directory.iterdir()} == names and all(p.is_file() and not p.is_symlink() for p in directory.iterdir()), "Downloaded asset set mismatch")
    mac_names = {"mac-evidence.json", "mac-evidence.json.sha256", *proof.artifact_names(version)["macos"]}
    mac_names |= {name + ".sha256" for name in proof.artifact_names(version)["macos"]}
    for item in remote:
        uploader = item.get("uploader", {}).get("login")
        allowed = {mac_uploader} if local_mac and item["name"] in mac_names else {BOT}
        if item["name"].removesuffix(".sha256") in proof.SHARED_NAMES:
            allowed |= {mac_uploader} if local_mac else set()
        require(uploader in allowed, "Unauthorized native proof/asset uploader: " + item["name"])
        path = directory / item["name"]
        require(item.get("state") == "uploaded" and item.get("size") == path.stat().st_size, "Incomplete/stale remote asset")
        remote_digest = item.get("digest")
        if remote_digest is not None:
            require(remote_digest == "sha256:" + proof.digest(path), "GitHub asset digest mismatch")
        if not path.name.endswith(".sha256"):
            checksum(path)
    source = directory / proof.SOURCE_NAME
    require(source.stat().st_size == 15926127 and proof.digest(source) == ffmpeg.SOURCE_SHA256, "Pinned release source archive mismatch")
    with tarfile.open(source, "r:gz") as archive:
        stream = archive.extractfile(f"FFmpeg-{ffmpeg.SOURCE_COMMIT}/COPYING.LGPLv2.1")
        require(stream is not None and stream.read() == (directory / "COPYING.LGPLv2.1").read_bytes(), "License is not verbatim pinned source license")
    for name in ("build_ffmpeg.sh", "verify_ffmpeg.py"):
        require(proof.digest(directory / name) == code["scripts/" + name], "Released recipe differs from exact commit")
    evidence = {}
    for platform_name, name in (("macos", "mac-evidence.json"), ("windows", "windows-evidence.json")):
        evidence[platform_name] = read_json(directory / name)
        validate_evidence(evidence[platform_name], platform_name, tag, commit, version, code, directory)
    verify_installer(directory / proof.artifact_names(version)["windows"][0])
    verify_windows_zip(directory / proof.artifact_names(version)["windows"][1], evidence["windows"])
    return names


def gh(repo, *args, missing_ok=False):
    result = subprocess.run(["gh", *args, "--repo", repo] if args[0] == "release" else ["gh", *args], capture_output=True, text=True, timeout=60 if args[0] == "api" else 600)
    if missing_ok and result.returncode and re.search(r"\bHTTP 404\b", result.stderr):
        return None
    require(result.returncode == 0, "GitHub command failed (no promotion): " + result.stderr[-500:])
    return result.stdout


def release_metadata(repo, tag, missing_ok=False):
    # Some GitHub setups omit drafts from release-by-tag even with a write
    # token. Only a genuine 404 permits this authenticated REST-list fallback;
    # all permission, network and malformed-response failures remain fatal.
    text = gh(repo, "api", f"repos/{repo}/releases/tags/{tag}", missing_ok=True)
    if text is not None:
        metadata = json.loads(text)
        require(isinstance(metadata, dict) and metadata.get("tag_name") == tag,
                "Release-by-tag metadata mismatch")
        return metadata
    # Never use `gh release view --json`: it drops asset uploader/state facts
    # required by the publisher's native-proof authorization checks.
    for page in range(1, 11):
        releases = json.loads(gh(repo, "api", f"repos/{repo}/releases?per_page=100&page={page}"))
        require(isinstance(releases, list) and len(releases) <= 100
                and all(isinstance(item, dict) and isinstance(item.get("tag_name"), str)
                        for item in releases), "Invalid authenticated release listing")
        matches = [item for item in releases if item["tag_name"] == tag]
        require(len(matches) <= 1, "Ambiguous exact-tag release listing")
        if matches:
            return matches[0]
        if len(releases) < 100:
            if missing_ok:
                return None
            raise ValueError("Exact-tag release absent from authenticated listing")
    # Reaching the bound is not evidence of absence: never create a duplicate.
    raise ValueError("Authenticated release listing exceeded 10-page lookup bound")



def remote_commit(repo, tag):
    ref = json.loads(gh(repo, "api", f"repos/{repo}/git/ref/tags/{tag}"))["object"]
    for _ in range(5):
        if ref["type"] == "commit":
            return ref["sha"]
        require(ref["type"] == "tag", "Unexpected remote tag type")
        ref = json.loads(gh(repo, "api", f"repos/{repo}/git/tags/{ref['sha']}"))["object"]
    raise ValueError("Excessively nested annotated tag")


def assert_draft(repo, tag, commit):
    require(remote_commit(repo, tag) == commit, "Remote tag changed; refusing release mutation")
    metadata = release_metadata(repo, tag)
    require(metadata["draft"] is True, "Published release is immutable here; refusing mutation")
    return metadata


def initialize(repo, tag, commit):
    proof.validate_identity(tag, commit)
    require(remote_commit(repo, tag) == commit, "Remote tag/checkout mismatch")
    metadata = release_metadata(repo, tag, missing_ok=True)
    if metadata is not None and not metadata["draft"]:
        print("Published release already exists; left untouched")
        return False
    if metadata is None:
        gh(repo, "release", "create", tag, "--verify-tag", "--target", commit, "--draft", "--generate-notes", "--title", "SJTU Learning Assistant " + tag)
    assert_draft(repo, tag, commit)
    return True


def upload(repo, tag, commit, directory):
    version, _ = proof.validate_identity(tag, commit)
    metadata = assert_draft(repo, tag, commit)
    allowed = required_names(version)
    paths = sorted(directory.iterdir())
    require(paths and all(path.is_file() and not path.is_symlink() and path.name in allowed for path in paths), "Unexpected upload files")
    existing = {item["name"]: item for item in metadata["assets"]}
    for path in paths:
        assert_draft(repo, tag, commit)
        if path.name in existing:
            # Never clobber: identical shared source is safely reused on reruns.
            with tempfile.TemporaryDirectory(prefix="sjtu-release-existing-") as temporary:
                gh(repo, "release", "download", tag, "--pattern", path.name, "--dir", temporary)
                require(proof.digest(Path(temporary) / path.name) == proof.digest(path), "Existing draft asset differs; refusing overwrite: " + path.name)
        else:
            gh(repo, "release", "upload", tag, str(path))
    print("Uploaded/reused draft assets without overwriting a published release")


def publish(repo, tag, commit, directory, mac_uploader, local_mac, wait_seconds):
    require(300 <= wait_seconds <= 900, "Local Mac wait must be bounded to 5-15 minutes")
    version, _ = proof.validate_identity(tag, commit)
    names = required_names(version)
    deadline = time.monotonic() + (wait_seconds if local_mac else 0)
    while True:
        metadata = assert_draft(repo, tag, commit)
        present = {item["name"] for item in metadata["assets"] if item.get("state") == "uploaded"}
        require(present <= names, "Unexpected draft assets; refusing promotion")
        if present == names:
            break
        require(time.monotonic() < deadline, "Required assets/proof missing; release remains DRAFT: " + ", ".join(sorted(names - present)))
        time.sleep(min(20, max(0, deadline - time.monotonic())))
    require(not directory.exists() or not any(directory.iterdir()), "Download directory must be empty")
    directory.mkdir(parents=True, exist_ok=True)
    gh(repo, "release", "download", tag, "--dir", str(directory))
    verify_directory(directory, tag, commit, metadata, mac_uploader, local_mac)
    latest = assert_draft(repo, tag, commit)
    # Detect replacements/uploads during the validation window.
    require(latest["id"] == metadata["id"] and latest["assets"] == metadata["assets"], "Release assets changed during validation")
    flags = ("--prerelease", "--latest=false") if "-" in tag else ("--prerelease=false", "--latest")
    gh(repo, "release", "edit", tag, "--draft=false", *flags)
    print("Both native platforms and all release bytes verified; promoted complete release")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("identity", "initialize", "upload", "verify", "publish"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--assets-dir", type=Path, default=Path("dist/release-download"))
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--mac-uploader")
    parser.add_argument("--use-local-macos", choices=("true", "false"), default="true")
    parser.add_argument("--wait-seconds", type=int, default=600)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "identity":
            version, _ = proof.validate_identity(args.tag, args.release_commit)
            print(f"Validated exact tag/commit for app {version}")
        elif args.action == "verify":
            require(args.metadata and args.mac_uploader, "Metadata and authorized Mac uploader required")
            verify_directory(args.assets_dir, args.tag, args.release_commit, read_json(args.metadata), args.mac_uploader, args.use_local_macos == "true")
        else:
            require(args.repo and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo), "Explicit owner/repository required")
            if args.action == "initialize":
                eligible = initialize(args.repo, args.tag, args.release_commit)
                if args.github_output:
                    with args.github_output.open("a", encoding="utf-8") as stream:
                        stream.write("eligible=" + str(eligible).lower() + "\n")
            elif args.action == "upload":
                upload(args.repo, args.tag, args.release_commit, args.assets_dir)
            else:
                require(args.mac_uploader, "Authorized Mac uploader required")
                publish(args.repo, args.tag, args.release_commit, args.assets_dir, args.mac_uploader, args.use_local_macos == "true", args.wait_seconds)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error, ffmpeg.VerificationError, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(f"Release verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
