#!/usr/bin/env python3
"""Fail-closed cross-host release validation and draft-only GitHub asset handling.

Native JSON is transported by digest-verified Actions artifacts from the same
trusted Release run/attempt and source commit, not authorized by claims in JSON.
Only three installer assets enter a GitHub Release. Ubuntu validates bytes/PE
imports but never claims to execute Windows FFmpeg or inspect a DMG natively.
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


def public_names(version):
    return {*proof.artifact_names(version)["macos"], *proof.artifact_names(version)["windows"]}


def platform_names(version, platform_name):
    primary = (*proof.artifact_names(version)[platform_name], *proof.SHARED_NAMES,
               "mac-evidence.json" if platform_name == "macos" else "windows-evidence.json")
    return {name for base in primary for name in (base, base + ".sha256")}


def required_names(version):
    """Internal validation material, NEVER the public Release upload whitelist."""
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


def validate_frontend_startup(report, platform_name):
    """Only the sanitized native self-test contract belongs in release evidence."""
    keys = {"schema_version", "result", "reason", "renderer", "frontend", "app_shell", "bridge_ready",
            "data_isolated", "network_blocked", "backend", "report_sha256"}
    require(isinstance(report, dict) and set(report) == keys, "Frontend startup proof fields mismatch")
    require(type(report["schema_version"]) is int and report["schema_version"] == 1,
            "Frontend startup schema mismatch")
    require(report["result"] == "passed" and report["reason"] == "ok" and report["backend"] == "reject-only"
            and report["renderer"] == {"macos": "wkwebview", "windows": "edgechromium"}[platform_name],
            "Frontend startup result/renderer/isolation mismatch")
    require(all(report[key] is True for key in ("app_shell", "bridge_ready", "data_isolated", "network_blocked")),
            "Frontend startup shell/bridge/isolation gate failed")
    frontend = report["frontend"]
    require(isinstance(frontend, dict) and set(frontend) == {"path", "sha256", "fingerprint"},
            "Frontend startup index fields mismatch")
    require(frontend["path"] == "dashboard-web/dist/index.html" and isinstance(frontend["sha256"], str)
            and HEX.fullmatch(frontend["sha256"]) and frontend["fingerprint"] == frontend["sha256"][:16],
            "Frontend startup index hash/fingerprint mismatch")
    require(isinstance(report["report_sha256"], str) and HEX.fullmatch(report["report_sha256"]),
            "Frontend startup report digest missing")


def validate_evidence(evidence, platform_name, tag, commit, version, code, directory, provenance=None):
    expected = {"schema_version": 1, "platform": platform_name, "architecture": "arm64" if platform_name == "macos" else "x64",
                "tag": tag, "app_version": version, "release_commit": commit, "code_sha256": code}
    require(all(evidence.get(k) == v for k, v in expected.items()), "Evidence identity/code/version mismatch")
    require({"desktop_app.py", "sjtu_learning_assistant/frontend_startup.py", "sjtu_learning_assistant/frontend_self_test.py"} <= set(code),
            "Native frontend startup source hashes missing")
    if provenance is not None:
        require(evidence.get("workflow_provenance") == provenance, "Native evidence run/actor provenance mismatch")
    native = evidence["native_verification"]
    require(native.get("result") == "passed" and native.get("host_os") == ("darwin" if platform_name == "macos" else "win32"), "Missing native verification")
    checks = native.get("checks")
    require(isinstance(checks, list), "Missing executed native checks")
    needed = {"ffmpeg_stage": "python", "ffmpeg_bundle": "python",
              "frontend_startup": proof.APP if platform_name == "macos" else proof.APP + ".exe"}
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
        require(set(item) == {"check", "tool", "returncode"}, "Native check fields mismatch")
        require(type(item.get("returncode")) is int and item["returncode"] == 0, "Native command failed")
        require(bool(re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", item.get("tool", "")))
                if tool == "python" else item.get("tool") == tool, "Unexpected proof command")
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
    validate_frontend_startup(app.get("frontend_startup"), platform_name)
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
        "_internal/dashboard-web/dist/index.html": evidence["application"]["frontend_startup"]["frontend"]["sha256"],
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


def verify_directory(directory, tag, commit, metadata, provenance):
    version, code = proof.validate_identity(tag, commit)
    require(isinstance(provenance, dict) and provenance.get("source_commit") == commit,
            "Authenticated same-run provenance required")
    names = required_names(version)
    require(metadata.get("draft") is True and metadata.get("tag_name") == tag, "Release must still be the exact draft")
    remote = metadata.get("assets", [])
    public = public_names(version)
    require(len(remote) == len(public) and {item["name"] for item in remote} == public, "Missing/extra/duplicate draft assets")
    require({p.name for p in directory.iterdir()} == names and all(p.is_file() and not p.is_symlink() for p in directory.iterdir()), "Missing/extra internal evidence or downloaded assets")
    for item in remote:
        require(item.get("uploader", {}).get("login") == BOT, "Unauthorized native asset uploader: " + item["name"])
        path = directory / item["name"]
        require(item.get("state") == "uploaded" and item.get("size") == path.stat().st_size, "Incomplete/stale remote asset")
        require(item.get("digest") == "sha256:" + proof.digest(path), "Missing/mismatched GitHub asset digest")
    for path in directory.iterdir():
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
        validate_evidence(evidence[platform_name], platform_name, tag, commit, version, code, directory, provenance)
    verify_installer(directory / proof.artifact_names(version)["windows"][0])
    verify_windows_zip(directory / proof.artifact_names(version)["windows"][1], evidence["windows"])
    return public


def gh(repo, *args, missing_ok=False):
    # gh always emits UTF-8. Capturing bytes and decoding explicitly avoids the
    # Windows locale (cp1252) charmap crash on non-ASCII release titles/bodies.
    command = ["gh", *args, "--repo", repo] if args[0] == "release" else ["gh", *args]
    result = subprocess.run(command, capture_output=True, timeout=60 if args[0] == "api" else 600)
    stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode("utf-8")
    stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", "replace")
    if missing_ok and result.returncode and re.search(r"\bHTTP 404\b", stderr):
        return None
    require(result.returncode == 0, "GitHub command failed (no promotion): " + stderr[-500:])
    return stdout


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
        # The REST response is authoritative; re-listing immediately after a
        # create can miss the draft (eventual consistency) and previously caused
        # a false failure plus duplicate drafts with the same tag.
        created = json.loads(gh(
            repo, "api", "--method", "POST", f"repos/{repo}/releases",
            "-f", "tag_name=" + tag,
            "-f", "name=SJTU Learning Assistant " + tag,
            "-F", "draft=true", "-F", "generate_release_notes=true",
        ))
        require(isinstance(created, dict) and created.get("tag_name") == tag
                and created.get("draft") is True
                and type(created.get("id")) is int and created["id"] > 0,
                "Draft creation response mismatch")
        return True
    assert_draft(repo, tag, commit)
    return True


def upload(repo, tag, commit, directory):
    version, _ = proof.validate_identity(tag, commit)
    metadata = assert_draft(repo, tag, commit)
    paths = sorted(directory.iterdir())
    require(paths and all(path.is_file() and not path.is_symlink() for path in paths), "Unexpected upload files")
    require({path.name for path in paths} in (platform_names(version, "macos"), platform_names(version, "windows")),
            "Incomplete/extra native evidence staging files")
    allowed = public_names(version)
    require(len(metadata["assets"]) == len({item["name"] for item in metadata["assets"]})
            and {item["name"] for item in metadata["assets"]} <= allowed, "Unexpected/duplicate draft assets")
    existing = {item["name"]: item for item in metadata["assets"]}
    for path in (path for path in paths if path.name in allowed):
        assert_draft(repo, tag, commit)
        if path.name in existing:
            # Never clobber: only byte-identical installer assets are reused on reruns.
            with tempfile.TemporaryDirectory(prefix="sjtu-release-existing-") as temporary:
                gh(repo, "release", "download", tag, "--pattern", path.name, "--dir", temporary)
                require(proof.digest(Path(temporary) / path.name) == proof.digest(path), "Existing draft asset differs; refusing overwrite: " + path.name)
        else:
            gh(repo, "release", "upload", tag, str(path))
    print("Uploaded/reused draft assets without overwriting a published release")


def native_artifact_name(platform_name, run_id, attempt):
    return f"SJTU-release-evidence-{platform_name}-{run_id}-{attempt}"


def authenticate_native_run(repo, commit, run_id, attempt, actor):
    """Authenticate the origin of proof outside the untrusted proof JSON."""
    require(all(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value)
                for value in (run_id, attempt)), "Explicit Release run/attempt required")
    require(isinstance(actor, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]+", actor), "Pinned Release actor required")
    run = json.loads(gh(repo, "api", f"repos/{repo}/actions/runs/{run_id}"))
    require(run.get("id") == int(run_id) and run.get("run_attempt") == int(attempt), "Release run/attempt mismatch")
    require(run.get("head_sha") == commit and run.get("path") == ".github/workflows/release.yml"
            and run.get("event") in {"push", "workflow_dispatch"}, "Release workflow/source provenance mismatch")
    require(run.get("repository", {}).get("full_name") == repo
            and run.get("head_repository", {}).get("full_name") == repo
            and run.get("actor", {}).get("login") == actor, "Release repository/actor provenance mismatch")
    require(run.get("status") == "in_progress" or
            (run.get("status") == "completed" and run.get("conclusion") == "success"), "Release run is not eligible")
    jobs = json.loads(gh(repo, "api", f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"))
    require(type(jobs.get("total_count")) is int and jobs["total_count"] <= 100
            and len(jobs.get("jobs", [])) == jobs["total_count"], "Incomplete Release job provenance")
    for name in ("initialize", "release-checks", "windows-release", "macos-release"):
        matches = [job for job in jobs["jobs"] if job.get("name") == name]
        require(len(matches) == 1 and matches[0].get("status") == "completed"
                and matches[0].get("conclusion") == "success" and matches[0].get("run_attempt") == int(attempt),
                "Required native/check job did not succeed in this attempt: " + name)
    listing = json.loads(gh(repo, "api", f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"))
    require(type(listing.get("total_count")) is int and listing["total_count"] <= 100
            and len(listing.get("artifacts", [])) == listing["total_count"], "Incomplete Actions artifact provenance")
    artifacts = {}
    repository_id = run["repository"].get("id")
    require(type(repository_id) is int and repository_id > 0, "Missing authenticated repository ID")
    for platform_name in ("macos", "windows"):
        name = native_artifact_name(platform_name, run_id, attempt)
        matches = [item for item in listing["artifacts"] if item.get("name") == name]
        require(len(matches) == 1, "Missing/duplicate native evidence artifact: " + name)
        item = matches[0]
        origin = item.get("workflow_run", {})
        require(origin.get("id") == int(run_id) and origin.get("head_sha") == commit
                and origin.get("repository_id") == repository_id and origin.get("head_repository_id") == repository_id,
                "Native artifact run/source provenance mismatch")
        require(type(item.get("id")) is int and item["id"] > 0 and item.get("expired") is False
                and type(item.get("size_in_bytes")) is int and 0 < item["size_in_bytes"] <= 4 * 1024**3,
                "Missing/expired/oversized native evidence artifact")
        require(isinstance(item.get("digest"), str) and re.fullmatch(r"sha256:[0-9a-f]{64}", item["digest"]),
                "Missing native artifact digest")
        artifacts[platform_name] = item
    provenance = {"repository": repo, "run_id": run_id, "run_attempt": attempt, "actor": actor, "source_commit": commit}
    return provenance, artifacts


def download_native_artifact(repo, item, destination, names):
    """Verify the authenticated Actions archive digest before bounded extraction."""
    require(not destination.exists(), "Native artifact destination must be absent")
    with tempfile.TemporaryDirectory(prefix="sjtu-native-proof-") as temporary:
        archive_path = Path(temporary) / "artifact.zip"
        with archive_path.open("wb") as stream:
            result = subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts/{item['id']}/zip"],
                                    stdout=stream, stderr=subprocess.PIPE, timeout=600)
        require(result.returncode == 0, "Native artifact download failed (no promotion)")
        require(0 < archive_path.stat().st_size <= 4 * 1024**3
                and item["digest"] == "sha256:" + proof.digest(archive_path), "Native artifact archive digest mismatch")
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            require(len(entries) == len(names) and {entry.filename for entry in entries} == names,
                    "Missing/extra/duplicate internal evidence files")
            require(all(not entry.is_dir() and not entry.flag_bits & 1
                        and (entry.external_attr >> 16) & 0o170000 != 0o120000
                        and 0 <= entry.file_size <= 2 * 1024**3 for entry in entries)
                    and sum(entry.file_size for entry in entries) <= 4 * 1024**3, "Unsafe/oversized internal evidence archive")
            # The expected names are flat, exact allowlisted filenames; no traversal is possible.
            destination.mkdir()
            archive.extractall(destination)


def merge_native_evidence(repo, version, directory, artifacts):
    with tempfile.TemporaryDirectory(prefix="sjtu-native-evidence-") as temporary:
        for platform_name in ("macos", "windows"):
            native = Path(temporary) / platform_name
            download_native_artifact(repo, artifacts[platform_name], native, platform_names(version, platform_name))
            for path in native.iterdir():
                target = directory / path.name
                if target.exists():
                    # Compare both the downloaded Release binaries and duplicate shared sources.
                    require(target.is_file() and not target.is_symlink() and proof.digest(target) == proof.digest(path),
                            "Release binary/shared evidence differs from native bytes: " + path.name)
                else:
                    shutil.copyfile(path, target)


def publish(repo, tag, commit, directory, actor, run_id, attempt):
    version, _ = proof.validate_identity(tag, commit)
    provenance, artifacts = authenticate_native_run(repo, commit, run_id, attempt, actor)
    names = public_names(version)
    metadata = assert_draft(repo, tag, commit)
    remote = metadata.get("assets", [])
    require(len(remote) == len(names) and {item["name"] for item in remote} == names
            and all(item.get("state") == "uploaded" for item in remote),
            "Required three assets missing/extra/incomplete; release remains DRAFT")
    require(not directory.exists() or not any(directory.iterdir()), "Download directory must be empty")
    directory.mkdir(parents=True, exist_ok=True)
    gh(repo, "release", "download", tag, "--dir", str(directory))
    require({path.name for path in directory.iterdir()} == names, "Downloaded public asset set mismatch")
    merge_native_evidence(repo, version, directory, artifacts)
    verify_directory(directory, tag, commit, metadata, provenance)
    latest = assert_draft(repo, tag, commit)
    # Detect replacements/uploads or reruns during the validation window.
    require(latest["id"] == metadata["id"] and latest["assets"] == metadata["assets"], "Release assets changed during validation")
    require(authenticate_native_run(repo, commit, run_id, attempt, actor) == (provenance, artifacts),
            "Native run/artifacts changed during validation")
    flags = ("--prerelease", "--latest=false") if "-" in tag else ("--prerelease=false", "--latest")
    notes = (latest.get("body") or "") + f"\n\n### Verified native build\nSource commit: `{commit}`\n" \
        + f"Release run: https://github.com/{repo}/actions/runs/{run_id} (attempt {attempt})\n" \
        + "Native macOS arm64 and Windows x64 gates passed; downloaded Release bytes verified.\n\n"
    for item in sorted(remote, key=lambda item: item["name"]):
        notes += f"- `{item['name']}` — {item['size']} bytes — `{item['digest']}`\n"
    notes += "\nPinned FFmpeg source, build recipes and LGPL notices are included inside the application packages.\n"
    with tempfile.TemporaryDirectory(prefix="sjtu-release-notes-") as temporary:
        notes_path = Path(temporary) / "notes.txt"
        notes_path.write_text(notes, encoding="utf-8")
        gh(repo, "release", "edit", tag, "--draft=false", "--notes-file", str(notes_path), *flags)
    print("Both native platforms, internal proof and exactly three release assets verified; promoted complete release")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("identity", "initialize", "upload", "verify", "publish"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--assets-dir", type=Path, default=Path("dist/release-download"))
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--provenance", type=Path, help="Authenticated context for offline verification only")
    parser.add_argument("--release-run")
    parser.add_argument("--run-attempt")
    parser.add_argument("--actor")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "identity":
            version, _ = proof.validate_identity(args.tag, args.release_commit)
            print(f"Validated exact tag/commit for app {version}")
        elif args.action == "verify":
            require(args.metadata and args.provenance, "Metadata and provenance context required")
            verify_directory(args.assets_dir, args.tag, args.release_commit, read_json(args.metadata), read_json(args.provenance))
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
                publish(args.repo, args.tag, args.release_commit, args.assets_dir, args.actor, args.release_run, args.run_attempt)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error, ffmpeg.VerificationError, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(f"Release verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
