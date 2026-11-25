#!/usr/bin/env python3
"""Reuse exact successful CI bytes and execute unchanged tagged native gates."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


def command(args, cwd=None, capture=False):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    result = subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=capture,
                            text=True, encoding="utf-8", errors="replace")
    return result.stdout.strip() if capture else None


def run(args):
    root = Path(args.release_root).resolve()
    sha = command(("git", "rev-parse", "HEAD"), cwd=root, capture=True)
    verifier = root / "scripts/verify_release_assets.py"
    command((sys.executable, str(verifier), "identity", "--tag", args.tag, "--release-commit", sha), cwd=root)
    info = json.loads(command(("gh", "api", "repos/" + args.repo + "/actions/runs/" + args.ci_run), capture=True))
    if info.get("conclusion") != "success" or info.get("head_sha") != sha:
        raise RuntimeError("Successful CI provenance does not match the immutable tag")
    if args.mode == "windows":
        if sys.platform != "win32":
            raise RuntimeError("Windows evidence requires native Windows")
        version = args.tag.removeprefix("v")
        artifacts = Path(args.artifacts).resolve()
        archive = artifacts / ("SJTU-Learning-Assistant-" + version + "-Windows-x64-portable.zip")
        work = Path(tempfile.mkdtemp(prefix="sjtu-recovered-release-"))
        bundle = work / "bundle"
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            if len(entries) > 20000 or sum(item.file_size for item in entries) > 1073741824:
                raise RuntimeError("Portable ZIP exceeds safe extraction limits")
            for entry in entries:
                relative = PurePosixPath(entry.filename)
                if (relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename
                        or ":" in entry.filename or entry.flag_bits & 1
                        or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise RuntimeError("Unsafe portable ZIP member")
            source.extractall(bundle)
        stage = work / "ffmpeg"
        shutil.copytree(bundle / "_internal/ffmpeg", stage)
        output = work / "release-evidence"
        command((sys.executable, str(root / "scripts/prepare_release_evidence.py"),
                 "--platform", "windows", "--tag", args.tag, "--release-commit", sha,
                 "--bundle", str(bundle), "--stage", str(stage), "--artifacts-dir", str(artifacts),
                 "--output-dir", str(output)), cwd=root)
        command((sys.executable, str(verifier), "upload", "--repo", args.repo, "--tag", args.tag,
                 "--release-commit", sha, "--assets-dir", str(output)), cwd=root)
    else:
        command((sys.executable, str(verifier), "publish", "--repo", args.repo, "--tag", args.tag,
                 "--release-commit", sha, "--mac-uploader", args.mac_uploader,
                 "--local-macos", "--wait-seconds", "600"), cwd=root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("windows", "promote"))
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--ci-run", required=True)
    parser.add_argument("--artifacts", default="dist/recovered")
    parser.add_argument("--mac-uploader", default="")
    run(parser.parse_args())
