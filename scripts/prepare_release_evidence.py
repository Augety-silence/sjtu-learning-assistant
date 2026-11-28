#!/usr/bin/env python3
"""Create native release proof; never substitutes a claimed result for execution."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import verify_ffmpeg as ffmpeg

ROOT = Path(__file__).resolve().parents[1]
APP = "SJTU Learning Assistant"
SOURCE_NAME = "FFmpeg-n7.1.3.tar.gz"
SHARED_NAMES = (SOURCE_NAME, "build_ffmpeg.sh", "verify_ffmpeg.py", "COPYING.LGPLv2.1")
CODE_PATHS = (
    "scripts/build_ffmpeg.sh", "scripts/verify_ffmpeg.py",
    "scripts/prepare_release_evidence.py", "scripts/verify_release_assets.py",
    "scripts/build_macos_app.sh", "scripts/build_macos_dmg.sh",
    "scripts/build_windows_app.ps1", "scripts/verify_macos_bundle.py",
    "scripts/verify_windows_manifest.py", "packaging/desktop.spec",
    "packaging/windows.spec", "packaging/windows-installer.iss",
    "packaging/common-hooks/hook-imageio_ffmpeg.py",
    "packaging/runtime_hooks/ffmpeg.py", "sjtu_learning_assistant/__init__.py",
    ".github/workflows/release.yml",
    "desktop_app.py", "sjtu_learning_assistant/frontend_startup.py",
    "sjtu_learning_assistant/frontend_self_test.py",
    "sjtu_learning_assistant/helper_stdio.py", "scripts/check_packaged_mcp.py",
)


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def project_version():
    tree = ast.parse((ROOT / "sjtu_learning_assistant/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Missing literal __version__")


def validate_identity(tag, commit):
    version = project_version()
    require(re.fullmatch(r"v" + re.escape(version) + r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", tag), "Tag/version mismatch")
    require(tuple(map(int, version.split("."))) >= (1, 8, 21), "Legacy source-only releases are not eligible")
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "Expected full immutable commit SHA")
    require(git("rev-parse", "HEAD") == commit == git("rev-parse", f"refs/tags/{tag}^{{commit}}"), "Checkout/tag/release commit mismatch")
    require(not git("status", "--porcelain", "--untracked-files=all"), "Release checkout has tracked modifications or untracked source files")
    hashes = {}
    for name in CODE_PATHS:
        git("ls-files", "--error-unmatch", name)
        hashes[name] = digest(ROOT / name)
    return version, hashes


def workflow_provenance(commit):
    """Record CI context; the publisher independently authenticates it via REST."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None  # Local evidence is useful diagnostically, never publishable.
    fields = {key: os.environ.get(env, "") for key, env in (
        ("repository", "GITHUB_REPOSITORY"), ("run_id", "GITHUB_RUN_ID"),
        ("run_attempt", "GITHUB_RUN_ATTEMPT"), ("actor", "GITHUB_ACTOR"),
    )}
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", fields["repository"]), "Missing CI repository")
    require(all(re.fullmatch(r"[1-9][0-9]*", fields[key]) for key in ("run_id", "run_attempt")), "Missing CI run identity")
    require(re.fullmatch(r"[A-Za-z0-9_.\[\]-]+", fields["actor"]), "Missing CI actor")
    return {**fields, "source_commit": commit}


def artifact_names(version):
    prefix = f"SJTU-Learning-Assistant-{version}"
    return {
        "macos": (prefix + "-macOS-arm64.dmg",),
        "windows": (prefix + "-Windows-x64-Setup.exe", prefix + "-Windows-x64-portable.zip"),
    }


def failure_diagnostic(text, limit=1200):
    """Bounded ASCII-safe logs, never stored in release evidence."""
    text = text or ""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----.*?-----END (?:[A-Z ]+)?PRIVATE KEY-----",
                  "<redacted-private-key>", text, flags=re.DOTALL)
    text = re.sub(r"(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]+)",
                  "<redacted-token>", text)
    text = re.sub(r"(?i)(\b[A-Za-z0-9_-]*(?:token|password|passwd|secret|api[_-]?key|authorization)[A-Za-z0-9_-]*\b[\"']?\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,;]+)",
                  r"\1<redacted>", text)
    text = re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@", r"\1<redacted>@", text)
    text = re.sub(r"(?:/Users/|/home/)[^\s\"']+|[A-Za-z]:[\\/]Users[\\/][^\s\"']+", "<private-path>", text)
    # Windows parent stderr may itself use a legacy code page; escaped Unicode
    # keeps failure reporting from raising a second UnicodeEncodeError.
    text = text.encode("ascii", errors="backslashreplace").decode("ascii")
    return text[-limit:]


def execute(check, tool, *args):
    env = os.environ.copy()
    # The build step's process-local PowerShell variables do not survive into
    # this workflow step. Python verifier success messages include Chinese and
    # must remain encodable when stdout/stderr are pipes on Windows.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([str(tool), *map(str, args)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300, env=env)
    if result.returncode != 0:
        diagnostics = "\n".join(f"{name}: {failure_diagnostic(output)}"
                                for name, output in (("stdout", result.stdout), ("stderr", result.stderr))
                                if output)
        raise ValueError(f"Native gate failed: {check} (exit {result.returncode}; tool {Path(tool).name})"
                         + ("\n" + diagnostics if diagnostics else ""))
    # Tool output/private paths never enter public proof JSON, even on success.
    return {"check": check, "tool": Path(tool).name, "returncode": result.returncode}



@contextmanager
def startup_report_directory(runner):
    """Bound cleanup of isolated WebView2 files after the app has exited."""
    temporary = tempfile.TemporaryDirectory(prefix="sjtu-native-startup-", dir=runner)
    try:
        yield temporary.name
    finally:
        deadline = time.monotonic() + 30
        while True:
            try:
                temporary.cleanup()
                break
            except PermissionError as error:
                # Only transient Windows sharing/lock violations are retried.
                # Never terminate other browsers or ignore a lasting failure.
                if sys.platform != "win32" or getattr(error, "winerror", None) not in (32, 33) or time.monotonic() >= deadline:
                    raise
                time.sleep(.2)


def frontend_startup_gate(app_exe, bundle, platform_name):
    """Execute the actual native packaged app, not a mock or fixture executable."""
    require(sys.platform == ("darwin" if platform_name == "macos" else "win32"), "Startup gate requires native OS")
    require(platform_name != "macos" or platform.machine() == "arm64", "Startup gate requires arm64 macOS")
    app_exe, bundle = Path(app_exe).resolve(), Path(bundle).resolve()
    require(app_exe.is_file() and app_exe.is_relative_to(bundle), "Missing packaged app executable")
    candidates = (bundle / "Contents/Resources/dashboard-web/dist/index.html", bundle / "Contents/Frameworks/dashboard-web/dist/index.html") if platform_name == "macos" else (bundle / "_internal/dashboard-web/dist/index.html",)
    indices = tuple(p for p in candidates if p.is_file())
    require(bool(indices), "Missing actual packaged frontend")
    hashes = tuple(digest(p) for p in indices)
    require(len(set(hashes)) == 1, "Packaged frontend locations disagree")
    frontend_hash = hashes[0]
    runner = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).resolve()
    runner.mkdir(parents=True, exist_ok=True)
    require(not runner.is_relative_to(bundle) and not runner.is_relative_to(ROOT.resolve()), "Startup report must be outside source/package stage")
    with startup_report_directory(runner) as directory:
        private = Path(directory).resolve()
        report = private / "startup-report.json"
        env = os.environ.copy()
        for key in tuple(env):
            if key.startswith(("SJTU_", "OPENAI_", "ANTHROPIC_", "AZURE_")) or key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
                env.pop(key, None)
        for key, name in (("HOME", "home"), ("USERPROFILE", "home"), ("APPDATA", "roaming"), ("LOCALAPPDATA", "local"),
                          ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data"),
                          ("SJTU_APP_SUPPORT_DIR", "support"), ("TEMP", "temp"), ("TMP", "temp"), ("TMPDIR", "temp")):
            path = private / name
            path.mkdir(mode=0o700, exist_ok=True)
            env[key] = str(path)
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring")
        try:
            result = subprocess.run((str(app_exe), "--startup-self-test", str(report)), cwd=private,
                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Native frontend startup timed out") from exc
        require(result.returncode == 0, "Native frontend startup failed")
        require(report.is_file() and not report.is_symlink() and 0 < report.stat().st_size <= 8192, "Missing bounded native startup report")
        raw = report.read_bytes()
        value = json.loads(raw.decode("utf-8"))
        expected = dict(schema_version=1, result="passed", reason="ok",
            renderer="wkwebview" if platform_name == "macos" else "edgechromium",
            frontend=dict(path="dashboard-web/dist/index.html", sha256=frontend_hash, fingerprint=frontend_hash[:16]),
            app_shell=True, bridge_ready=True, data_isolated=True, network_blocked=True, backend="reject-only")
        require(value == expected, "Native startup report does not prove full AppShell and bridge readiness")
        require(all(digest(p) == frontend_hash for p in indices), "Packaged frontend changed during startup gate")
        value["report_sha256"] = hashlib.sha256(raw).hexdigest()
        return dict(check="frontend_startup", tool=app_exe.name, returncode=0), value


def tree_digest(bundle):
    """Hash paths, file contents and symlink targets, not mutable xattrs/timestamps."""
    h = hashlib.sha256()
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            record = "link\0" + path.relative_to(bundle).as_posix() + "\0" + os.readlink(path)
        elif path.is_file():
            record = "file\0" + path.relative_to(bundle).as_posix() + "\0" + digest(path)
        else:
            continue
        h.update(record.encode("utf-8") + b"\n")
    return h.hexdigest()


def plist_report(bundle, version):
    path = bundle / "Contents/Info.plist"
    value = plistlib.loads(path.read_bytes())
    expected = {"CFBundleIdentifier": "io.github.sjtu-learning-assistant", "CFBundleName": APP,
                "CFBundleShortVersionString": version, "CFBundleVersion": version}
    require(isinstance(value, dict) and all(value.get(k) == v for k, v in expected.items()), "macOS plist version/identity mismatch")
    return {**expected, "sha256": digest(path)}


def native_macos(bundle, dmg, version):
    require(sys.platform == "darwin" and platform.machine() == "arm64", "macOS arm64 proof must execute on native arm64 macOS")
    checks = [execute("macos_bundle", sys.executable, ROOT / "scripts/verify_macos_bundle.py", bundle)]
    report = plist_report(bundle, version)
    local_tree = tree_digest(bundle)
    checks.append(execute("dmg_integrity", "/usr/bin/hdiutil", "verify", dmg))
    with tempfile.TemporaryDirectory(prefix="sjtu-release-dmg-") as directory:
        mount = Path(directory) / "mounted"
        mount.mkdir()
        attached = False
        try:
            checks.append(execute("dmg_attach_readonly", "/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, dmg))
            attached = True
            mounted = mount / (APP + ".app")
            require(plist_report(mounted, version) == report and tree_digest(mounted) == local_tree, "DMG app differs from the verified local bundle")
            checks.append(execute("dmg_bundle", sys.executable, ROOT / "scripts/verify_macos_bundle.py", mounted))
            checks.append(execute("dmg_ffmpeg", sys.executable, ROOT / "scripts/verify_ffmpeg.py", "--bundle", mounted))
        finally:
            if attached:
                checks.append(execute("dmg_detach", "/usr/bin/hdiutil", "detach", mount))
    return checks, {"plist": report, "bundle_tree_sha256": local_tree, "dmg_matches_bundle": True}


def write_checksum(path):
    # Shared source sidecars must be byte-identical on Windows and macOS.
    path.with_name(path.name + ".sha256").write_bytes(f"{digest(path)}  {path.name}\n".encode("ascii"))


def prepare(tag, commit, platform_name, bundle, stage, artifacts_dir, output):
    version, code_hashes = validate_identity(tag, commit)
    require(sys.platform == {"macos": "darwin", "windows": "win32"}[platform_name], "Evidence requires the native target OS")
    output.mkdir(parents=True, exist_ok=True)
    checks = [execute("ffmpeg_stage", sys.executable, ROOT / "scripts/verify_ffmpeg.py", "--stage", stage),
              execute("ffmpeg_bundle", sys.executable, ROOT / "scripts/verify_ffmpeg.py", "--bundle", bundle)]
    manifest = json.loads((stage / "build-manifest.json").read_text(encoding="utf-8"))
    packaged_stage = ffmpeg.locate_bundle(bundle)
    packaged_manifest = json.loads((packaged_stage / "build-manifest.json").read_text(encoding="utf-8"))
    require(packaged_manifest == manifest, "Bundle did not include the exact staged FFmpeg manifest")
    require(manifest["recipe_sha256"] == {name: code_hashes["scripts/" + name] for name in ("build_ffmpeg.sh", "verify_ffmpeg.py")}, "FFmpeg recipe differs from release commit")
    packaged_binary = packaged_stage / manifest["binary"]
    packaged_hash = digest(packaged_binary)
    names = artifact_names(version)[platform_name]
    artifacts = [artifacts_dir / name for name in names]
    require(all(path.is_file() for path in artifacts), "Missing expected platform artifact")
    before = {path.name: digest(path) for path in artifacts}
    if platform_name == "macos":
        extra_checks, report = native_macos(bundle, artifacts[0], version)
        checks += extra_checks
        app_exe = bundle / "Contents/MacOS" / APP
        app_path = "Contents/MacOS/" + APP
    else:
        require(packaged_hash == manifest["binary_sha256"], "Windows packaged FFmpeg differs from the native stage")
        app_exe = bundle / (APP + ".exe")
        app_path = APP + ".exe"
        checks.append(execute("windows_manifest", sys.executable, ROOT / "scripts/verify_windows_manifest.py", app_exe))
        report = {"version_scope": "exact-commit-build", "manifest_verified": True}
    startup_check, startup_report = frontend_startup_gate(app_exe, bundle, platform_name)
    checks.append(startup_check)
    report["frontend_startup"] = startup_report
    evidence = {
        "schema_version": 1, "platform": platform_name, "architecture": "arm64" if platform_name == "macos" else "x64",
        "tag": tag, "app_version": version, "release_commit": commit, "code_sha256": code_hashes,
        "workflow_provenance": workflow_provenance(commit),
        "ffmpeg": {"stage_manifest": manifest, "stage_binary_sha256": manifest["binary_sha256"],
                   "packaged_binary_sha256": packaged_hash, "packaged_path": packaged_binary.relative_to(bundle).as_posix(),
                   "packaged_manifest_sha256": digest(packaged_stage / "build-manifest.json")},
        "application": {"path": app_path, "sha256": digest(app_exe), **report},
        "native_verification": {"result": "passed", "host_os": sys.platform, "checks": checks}, "assets": [],
    }
    for path in artifacts:
        destination = output / path.name
        if path.resolve() != destination.resolve():
            shutil.copyfile(path, destination)
        require(digest(destination) == before[path.name], "Artifact changed during native verification")
        evidence["assets"].append({"name": path.name, "sha256": digest(destination), "bytes": destination.stat().st_size})
        write_checksum(destination)
    for name in SHARED_NAMES:
        source = stage / ("licenses" if name == "COPYING.LGPLv2.1" else "source") / name
        destination = output / name
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        evidence["assets"].append({"name": name, "sha256": digest(destination), "bytes": destination.stat().st_size})
        write_checksum(destination)
    if platform_name == "windows":
        from verify_release_assets import verify_installer, verify_windows_zip
        verify_installer(output / names[0])
        verify_windows_zip(output / names[1], evidence)
    require(validate_identity(tag, commit) == (version, code_hashes), "Release code changed during verification")
    require(digest(packaged_binary) == packaged_hash and digest(app_exe) == evidence["application"]["sha256"], "Bundle changed during verification")
    path = output / ("mac-evidence.json" if platform_name == "macos" else "windows-evidence.json")
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_checksum(path)
    print(f"Native evidence written: {path.name}; release stays draft until both platforms validate")
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--platform", choices=("macos", "windows"), required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--stage", type=Path, default=Path(os.environ.get("SJTU_FFMPEG_STAGE", str(ROOT / "dist/ffmpeg"))))
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/release-evidence")
    args = parser.parse_args(argv)
    try:
        prepare(args.tag, args.release_commit, args.platform, args.bundle.resolve(), args.stage.resolve(), args.artifacts_dir.resolve(), args.output_dir.resolve())
    except (OSError, ValueError, KeyError, TypeError, ffmpeg.VerificationError, subprocess.SubprocessError) as exc:
        print(f"Release evidence failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
