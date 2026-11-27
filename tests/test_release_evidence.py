from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_release_evidence as prepare
import verify_release_assets as assets
import verify_ffmpeg as ffmpeg


def startup_report(platform_name, index_bytes=b"synthetic frontend index; never a release app"):
    index_hash = assets.sha_bytes(index_bytes)
    report = {"schema_version": 1, "result": "passed", "reason": "ok",
              "renderer": "wkwebview" if platform_name == "macos" else "edgechromium",
              "frontend": {"path": "dashboard-web/dist/index.html", "sha256": index_hash, "fingerprint": index_hash[:16]},
              "app_shell": True, "bridge_ready": True, "data_isolated": True, "network_blocked": True,
              "backend": "reject-only"}
    report["report_sha256"] = assets.sha_bytes(json.dumps(report, sort_keys=True).encode())
    return report


def pe_bytes():
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<H", data, 0x94, 240)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<I", data, 0x98 + 108, 16)
    struct.pack_into("<II", data, 0x98 + 112 + 8, 0x1000, 40)
    struct.pack_into("<IIII", data, 0x98 + 240 + 8, 512, 0x1000, 512, 512)
    struct.pack_into("<IIIII", data, 512, 0, 0, 0, 0x1080, 0)
    data[640:653] = b"kernel32.dll\0"
    return bytes(data)


class NativePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_identity_rejects_wrong_tag_commit_dirty_checkout_and_legacy(self):
        sha = "a" * 40
        with patch.object(prepare, "project_version", return_value="1.8.21"):
            for tag in ("v1.8.20", "v1.8.21-", "v1.8.21\ncommit=injected", "refs/tags/v1.8.21"):
                with self.subTest(tag=tag), self.assertRaises(ValueError):
                    prepare.validate_identity(tag, sha)
            with patch.object(prepare, "git", side_effect=[sha, "b" * 40]), self.assertRaisesRegex(ValueError, "commit mismatch"):
                prepare.validate_identity("v1.8.21", sha)
            with patch.object(prepare, "git", side_effect=[sha, sha, " M tracked"]), self.assertRaisesRegex(ValueError, "tracked modifications"):
                prepare.validate_identity("v1.8.21", sha)
        with patch.object(prepare, "project_version", return_value="1.8.20"), self.assertRaisesRegex(ValueError, "Legacy"):
            prepare.validate_identity("v1.8.20", sha)

    def test_native_command_failure_never_becomes_passed_evidence(self):
        failed = subprocess.CompletedProcess([], 7, "private output", "private error")
        with patch.object(prepare.subprocess, "run", return_value=failed), self.assertRaisesRegex(ValueError, "Native gate failed"):
            prepare.execute("ffmpeg_bundle", sys.executable, "verify_ffmpeg.py", "--bundle", self.root)

    def test_execute_utf8_handles_chinese_success_under_legacy_child_encoding(self):
        command = "print('Windows 清单校验通过')"
        with patch.dict(os.environ, {"PYTHONUTF8": "0", "PYTHONIOENCODING": "ascii"}):
            # Reproduce the original helper's inherited-pipe encoding failure.
            baseline = subprocess.run([sys.executable, "-c", command], capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertNotEqual(0, baseline.returncode)
            self.assertIn("UnicodeEncodeError", baseline.stderr)
            result = prepare.execute("windows_manifest", sys.executable, "-c", command)
        self.assertEqual(0, result["returncode"])
        self.assertEqual({"check", "tool", "returncode"}, set(result))

    def test_execute_failure_reports_bounded_redacted_ascii_output(self):
        token = "ghp_" + "A" * 35
        output = ("x" * 2000 + "\nUnicodeEncodeError: 清单\nGH_TOKEN=" + token
                  + "\napi_key=synthetic-api-secret\n/home/example/private.py\n"
                  + "https://user:synthetic-password@example.invalid/path")
        failed = subprocess.CompletedProcess([], 1, output, "manifest check failed: synthetic detail")
        with patch.object(prepare.subprocess, "run", return_value=failed), self.assertRaises(ValueError) as caught:
            prepare.execute("windows_manifest", sys.executable, "verify_windows_manifest.py", self.root)
        message = str(caught.exception)
        self.assertIn("windows_manifest", message)
        self.assertIn("UnicodeEncodeError", message)
        self.assertIn("synthetic detail", message)
        self.assertIn("<redacted>", message)
        self.assertNotIn(token, message)
        self.assertNotIn("synthetic-api-secret", message)
        self.assertNotIn("synthetic-password", message)
        self.assertNotIn("/home/example/", message)
        self.assertTrue(message.isascii())
        self.assertLess(len(message), 2600)

    def test_execute_passes_utf8_env_without_mutating_parent_environment(self):
        success = subprocess.CompletedProcess([], 0, "private success output", "")
        with patch.dict(os.environ, {"PYTHONUTF8": "0", "PYTHONIOENCODING": "ascii"}), patch.object(prepare.subprocess, "run", return_value=success) as run:
            proof = prepare.execute("windows_manifest", sys.executable, "verifier.py")
            self.assertEqual("0", os.environ["PYTHONUTF8"])
            self.assertEqual("ascii", os.environ["PYTHONIOENCODING"])
        options = run.call_args.kwargs
        self.assertEqual("1", options["env"]["PYTHONUTF8"])
        self.assertEqual("utf-8", options["env"]["PYTHONIOENCODING"])
        self.assertEqual("utf-8", options["encoding"])
        self.assertEqual("replace", options["errors"])
        self.assertNotIn("private success output", str(proof))

    def test_failure_diagnostic_redacts_multiline_keys_and_ansi_before_bounding(self):
        text = ("\x1b[31mUnicodeEncodeError\x1b[0m\n-----BEGIN "
                + "PRIVATE KEY-----\nsynthetic-key-material\n-----END " + "PRIVATE KEY-----\n")
        diagnostic = prepare.failure_diagnostic(text)
        self.assertIn("UnicodeEncodeError", diagnostic)
        self.assertNotIn("synthetic-key-material", diagnostic)
        self.assertNotIn("\x1b", diagnostic)
        self.assertLessEqual(len(diagnostic), 1200)

    def test_mac_gate_runs_hdiutil_verify_mount_and_native_ffmpeg(self):
        app = self.root / "local.app"
        app.mkdir()
        dmg = self.root / "app.dmg"
        dmg.write_bytes(b"fixture")
        calls = []
        def execute(check, tool, *args):
            calls.append((check, str(tool), tuple(map(str, args))))
            return {"check": check, "tool": Path(tool).name, "returncode": 0}
        with patch.object(sys, "platform", "darwin"), patch.object(prepare.platform, "machine", return_value="arm64"), patch.object(prepare, "execute", side_effect=execute), patch.object(prepare, "plist_report", return_value={"sha256": "a" * 64}), patch.object(prepare, "tree_digest", return_value="b" * 64):
            checks, report = prepare.native_macos(app, dmg, "1.8.21")
        by_check = {check: (tool, args) for check, tool, args in calls}
        self.assertEqual(("/usr/bin/hdiutil", ("verify", str(dmg))), by_check["dmg_integrity"])
        self.assertIn("--bundle", by_check["dmg_ffmpeg"][1])
        self.assertIn("-readonly", by_check["dmg_attach_readonly"][1])
        self.assertTrue(report["dmg_matches_bundle"])
        self.assertEqual(6, len(checks))

    def test_mismatched_dmg_detaches_and_fails(self):
        app = self.root / "local.app"
        calls = []
        def execute(check, tool, *args):
            calls.append(check)
            return {"check": check, "tool": Path(tool).name, "returncode": 0}
        with patch.object(sys, "platform", "darwin"), patch.object(prepare.platform, "machine", return_value="arm64"), patch.object(prepare, "execute", side_effect=execute), patch.object(prepare, "plist_report", return_value={}), patch.object(prepare, "tree_digest", side_effect=["a" * 64, "b" * 64]), self.assertRaisesRegex(ValueError, "DMG app differs"):
            prepare.native_macos(app, self.root / "app.dmg", "1.8.21")
        self.assertIn("dmg_detach", calls)
        self.assertNotIn("dmg_ffmpeg", calls)

    def test_dmg_verify_failure_does_not_attempt_mount(self):
        calls = []
        def execute(check, tool, *args):
            calls.append(check)
            if check == "dmg_integrity":
                raise ValueError("hdiutil failed")
            return {"check": check, "tool": Path(tool).name, "returncode": 0}
        with patch.object(sys, "platform", "darwin"), patch.object(prepare.platform, "machine", return_value="arm64"), patch.object(prepare, "execute", side_effect=execute), patch.object(prepare, "plist_report", return_value={}), patch.object(prepare, "tree_digest", return_value="a" * 64), self.assertRaises(ValueError):
            prepare.native_macos(self.root / "app", self.root / "dmg", "1.8.21")
        self.assertNotIn("dmg_attach_readonly", calls)

    def test_plist_requires_both_exact_version_fields(self):
        app = self.root / "app"
        path = app / "Contents/Info.plist"
        path.parent.mkdir(parents=True)
        value = {"CFBundleIdentifier": "io.github.sjtu-learning-assistant", "CFBundleName": prepare.APP,
                 "CFBundleShortVersionString": "1.8.21", "CFBundleVersion": "1.8.20"}
        path.write_bytes(plistlib.dumps(value))
        with self.assertRaisesRegex(ValueError, "plist version"):
            prepare.plist_report(app, "1.8.21")
        value["CFBundleVersion"] = "1.8.21"
        path.write_bytes(plistlib.dumps(value))
        self.assertEqual(prepare.digest(path), prepare.plist_report(app, "1.8.21")["sha256"])

    def test_shared_sidecars_use_identical_lf_bytes_on_every_os(self):
        path = self.root / "COPYING.LGPLv2.1"
        path.write_bytes(b"fixture")
        prepare.write_checksum(path)
        self.assertEqual((prepare.digest(path) + "  COPYING.LGPLv2.1\n").encode("ascii"),
                         path.with_name(path.name + ".sha256").read_bytes())

    def test_non_native_os_fails_before_output_or_commands(self):
        with patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(sys, "platform", "linux"), patch.object(prepare, "execute") as run, self.assertRaisesRegex(ValueError, "native target OS"):
            prepare.prepare("v1.8.21", "a" * 40, "macos", self.root, self.root, self.root, self.root / "output")
        run.assert_not_called()
        self.assertFalse((self.root / "output").exists())


class NativeFrontendGateTests(unittest.TestCase):
    def test_native_gate_runs_real_executable_and_rejects_bad_reports(self):
        for platform_name in ("macos", "windows"):
            for case in ("passed", "nonzero", "missing", "wrong_hash", "placeholder", "unready", "network", "extra_path", "timeout"):
                with self.subTest(platform_name=platform_name, case=case), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    bundle = root / "bundle"
                    app_exe = bundle / ("Contents/MacOS/" + prepare.APP if platform_name == "macos" else prepare.APP + ".exe")
                    app_exe.parent.mkdir(parents=True)
                    app_exe.write_bytes(b"synthetic executable unit fixture, not native proof")
                    index = bundle / ("Contents/Resources/dashboard-web/dist/index.html" if platform_name == "macos" else "_internal/dashboard-web/dist/index.html")
                    index.parent.mkdir(parents=True)
                    index.write_bytes(b"synthetic frontend unit fixture, not native proof")
                    sha = prepare.digest(index)
                    good = dict(schema_version=1, result="passed", reason="ok",
                        renderer="wkwebview" if platform_name == "macos" else "edgechromium",
                        frontend=dict(path="dashboard-web/dist/index.html", sha256=sha, fingerprint=sha[:16]),
                        app_shell=True, bridge_ready=True, data_isolated=True, network_blocked=True, backend="reject-only")
                    def run(command, **options):
                        self.assertEqual((str(app_exe), "--startup-self-test"), tuple(command[:2]))
                        report = Path(command[2])
                        self.assertTrue(report.is_relative_to(root / "runner"))
                        self.assertFalse(report.is_relative_to(bundle))
                        self.assertLessEqual(options.get("timeout"), 90)
                        self.assertNotIn("OPENAI_API_KEY", options.get("env"))
                        self.assertNotIn("SJTU_DATABASE_URL", options.get("env"))
                        self.assertEqual("keyring.backends.fail.Keyring", options.get("env").get("PYTHON_KEYRING_BACKEND"))
                        if case == "timeout":
                            raise subprocess.TimeoutExpired(command, 90)
                        value = copy.deepcopy(good)
                        if case == "wrong_hash": value.get("frontend").update(sha256="b".ljust(64, "b"))
                        if case == "placeholder": value.update(app_shell=False)
                        if case == "unready": value.update(bridge_ready=False)
                        if case == "network": value.update(network_blocked=False)
                        if case == "extra_path": value.update(private_path="synthetic private path")
                        if case != "missing": report.write_text(json.dumps(value))
                        return subprocess.CompletedProcess(command, 1 if case == "nonzero" else 0, "ignored stdout", "")
                    with patch.object(sys, "platform", "darwin" if platform_name == "macos" else "win32"), patch.object(prepare.platform, "machine", return_value="arm64"), patch.dict(os.environ, dict(RUNNER_TEMP=str(root / "runner"), OPENAI_API_KEY="fake", SJTU_DATABASE_URL="fake")), patch.object(prepare.subprocess, "run", side_effect=run):
                        if case == "passed":
                            check, proof = prepare.frontend_startup_gate(app_exe, bundle, platform_name)
                            self.assertEqual("frontend_startup", check.get("check"))
                            self.assertEqual(app_exe.name, check.get("tool"))
                            self.assertEqual(sha, proof.get("frontend").get("sha256"))
                            self.assertEqual(64, len(proof.get("report_sha256")))
                            self.assertNotIn(str(root), str(proof))
                            self.assertNotIn("stdout", proof)
                        else:
                            with self.assertRaises(ValueError):
                                prepare.frontend_startup_gate(app_exe, bundle, platform_name)
                    self.assertEqual(list(), list((root / "runner").iterdir()))

    def test_native_gate_refuses_wrong_os_before_execution(self):
        with patch.object(sys, "platform", "linux"), patch.object(prepare.subprocess, "run") as run, self.assertRaises(ValueError):
            prepare.frontend_startup_gate(Path("unused.exe"), Path("unused"), "windows")
        run.assert_not_called()


class ReleaseAssetTests(unittest.TestCase):
    def test_windows_prepare_writes_native_evidence_and_checksum_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / prepare.APP
            bundle.mkdir()
            with zipfile.ZipFile(self.base / prepare.artifact_names(self.version)["windows"][1]) as archive:
                archive.extractall(bundle)  # Only the trusted synthetic fixture, never release input.
            output = root / "evidence"
            def execute(check, tool, *args):
                return {"check": check, "tool": "python.exe", "returncode": 0}
            with patch.object(sys, "platform", "win32"), patch.object(prepare, "execute", side_effect=execute), patch.object(prepare, "frontend_startup_gate", return_value=(
                dict(check="frontend_startup", tool=prepare.APP + ".exe", returncode=0),
                copy.deepcopy(self.evidence.get("windows").get("application").get("frontend_startup")))):
                evidence = prepare.prepare(self.tag, self.commit, "windows", bundle,
                                           bundle / "_internal/ffmpeg", self.base, output)
            self.assertEqual("passed", evidence["native_verification"]["result"])
            self.assertEqual(self.code, evidence["code_sha256"])
            self.assertEqual(self.commit, evidence["release_commit"])
            self.assertTrue((output / "windows-evidence.json").is_file())
            for path in output.iterdir():
                if not path.name.endswith(".sha256"):
                    assets.checksum(path)
            assets.validate_evidence(evidence, "windows", self.tag, self.commit, self.version, self.code, output)

    def test_failed_packaged_frontend_startup_writes_no_native_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / prepare.APP
            bundle.mkdir()
            with zipfile.ZipFile(self.base / prepare.artifact_names(self.version).get("windows").__getitem__(1)) as archive:
                archive.extractall(bundle)  # Trusted unit fixture only.
            output = root / "evidence"
            def execute(check, tool, *args):
                return dict(check=check, tool="python.exe", returncode=0)
            with patch.object(sys, "platform", "win32"), patch.object(prepare, "execute", side_effect=execute), patch.object(prepare, "frontend_startup_gate", side_effect=ValueError("startup failed")), self.assertRaisesRegex(ValueError, "startup failed"):
                prepare.prepare(self.tag, self.commit, "windows", bundle, bundle / "_internal/ffmpeg", self.base, output)
            self.assertFalse((output / "windows-evidence.json").exists())

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.version, cls.tag, cls.commit = "1.8.21", "v1.8.21", "a" * 40
        cls.provenance = {"repository": "owner/repo", "run_id": "123", "run_attempt": "1",
                          "actor": "native-owner", "source_commit": cls.commit}
        cls.code = {name: "c" * 64 for name in (*prepare.CODE_PATHS, "desktop_app.py",
                    "sjtu_learning_assistant/frontend_startup.py", "sjtu_learning_assistant/frontend_self_test.py")}
        cls.license = b"Synthetic LGPL license fixture; not a real release"
        archive = cls.base / prepare.SOURCE_NAME
        with tarfile.open(archive, "w:gz") as source:
            member = tarfile.TarInfo(f"FFmpeg-{ffmpeg.SOURCE_COMMIT}/COPYING.LGPLv2.1")
            member.size = len(cls.license)
            source.addfile(member, io.BytesIO(cls.license))
        # Temporary synthetic fixture only. Pad to exercise exact source-byte checks.
        with archive.open("ab") as stream:
            stream.truncate(15926127)
        cls.source_hash = prepare.digest(archive)
        (cls.base / "COPYING.LGPLv2.1").write_bytes(cls.license)
        for name in ("build_ffmpeg.sh", "verify_ffmpeg.py"):
            (cls.base / name).write_bytes(b"synthetic recipe " + name.encode())
            cls.code["scripts/" + name] = prepare.digest(cls.base / name)
        cls.evidence = {}
        binary = pe_bytes()
        for platform_name, proof_name in (("macos", "mac-evidence.json"), ("windows", "windows-evidence.json")):
            windows = platform_name == "windows"
            manifest = {
                "schema_version": 1, "version": "7.1.3", "source_tag": "n7.1.3", "source_commit": ffmpeg.SOURCE_COMMIT,
                "source_url": ffmpeg.SOURCE_URL, "source_sha256": cls.source_hash, "source_bytes": 15926127,
                "license": "LGPL-2.1-or-later", "license_sha256": assets.sha_bytes(cls.license), "compiler": "fixture native compiler",
                "buildconf": sorted(ffmpeg.REQUIRED_FLAGS | ({"--disable-x86asm"} if windows else set())),
                "system_dependencies": ["kernel32.dll"] if windows else ["/usr/lib/libSystem.B.dylib"],
                "binary": "bin/ffmpeg.exe" if windows else "bin/ffmpeg", "binary_sha256": assets.sha_bytes(binary),
                "binary_hash_scope": "unmodified-stage-before-packager-signing",
                "recipe_sha256": {name: cls.code["scripts/" + name] for name in ("build_ffmpeg.sh", "verify_ffmpeg.py")},
            }
            raw_manifest = json.dumps(manifest, sort_keys=True).encode()
            checks = ["ffmpeg_stage", "ffmpeg_bundle", "windows_manifest"] if windows else ["ffmpeg_stage", "ffmpeg_bundle", "macos_bundle", "dmg_integrity", "dmg_attach_readonly", "dmg_bundle", "dmg_ffmpeg", "dmg_detach"]
            checks.append("frontend_startup")
            app = {"path": prepare.APP + ".exe" if windows else "Contents/MacOS/" + prepare.APP,
                   "sha256": assets.sha_bytes(binary), "frontend_startup": startup_report(platform_name)}
            if windows:
                app.update({"version_scope": "exact-commit-build", "manifest_verified": True})
            else:
                app.update({"plist": {"CFBundleIdentifier": "io.github.sjtu-learning-assistant", "CFBundleName": prepare.APP,
                                      "CFBundleVersion": cls.version, "CFBundleShortVersionString": cls.version, "sha256": "d" * 64},
                            "bundle_tree_sha256": "e" * 64, "dmg_matches_bundle": True})
            evidence = {
                "schema_version": 1, "platform": platform_name, "architecture": "x64" if windows else "arm64",
                "tag": cls.tag, "app_version": cls.version, "release_commit": cls.commit, "code_sha256": cls.code,
                "workflow_provenance": cls.provenance,
                "ffmpeg": {"stage_manifest": manifest, "stage_binary_sha256": manifest["binary_sha256"],
                           "packaged_binary_sha256": manifest["binary_sha256"], "packaged_manifest_sha256": assets.sha_bytes(raw_manifest),
                           "packaged_path": "_internal/ffmpeg/bin/ffmpeg.exe" if windows else "Contents/Frameworks/ffmpeg/bin/ffmpeg"},
                "application": app, "native_verification": {"result": "passed", "host_os": "win32" if windows else "darwin",
                    "checks": [{"check": check,
                                "tool": (prepare.APP + ".exe" if windows else prepare.APP) if check == "frontend_startup"
                                else "hdiutil" if check in {"dmg_integrity", "dmg_attach_readonly", "dmg_detach"}
                                else "python.exe" if windows else "python", "returncode": 0} for check in checks]}, "assets": [],
            }
            for name in prepare.artifact_names(cls.version)[platform_name]:
                (cls.base / name).write_bytes(binary if name.endswith(".exe") else b"synthetic artifact fixture")
            if windows:
                zip_path = cls.base / prepare.artifact_names(cls.version)["windows"][1]
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(prepare.APP + ".exe", binary)
                    archive.writestr("_internal/dashboard-web/dist/index.html", b"synthetic frontend index; never a release app")
                    archive.writestr("_internal/ffmpeg/bin/ffmpeg.exe", binary)
                    archive.writestr("_internal/ffmpeg/build-manifest.json", raw_manifest)
                    for name in prepare.SHARED_NAMES:
                        archive.write(cls.base / name, "_internal/ffmpeg/" + ("licenses/" if name == "COPYING.LGPLv2.1" else "source/") + name)
            for name in (*prepare.artifact_names(cls.version)[platform_name], *prepare.SHARED_NAMES):
                path = cls.base / name
                evidence["assets"].append({"name": name, "sha256": prepare.digest(path), "bytes": path.stat().st_size})
            cls.evidence[platform_name] = evidence
            (cls.base / proof_name).write_text(json.dumps(evidence))
        for path in tuple(cls.base.iterdir()):
            prepare.write_checksum(path)
        cls.metadata = {"draft": True, "tag_name": cls.tag, "id": 42, "assets": []}
        for path in cls.base.iterdir():
            if path.name in assets.public_names(cls.version):
                cls.metadata["assets"].append({"name": path.name, "id": len(cls.metadata["assets"]) + 1, "state": "uploaded",
                                              "uploader": {"login": assets.BOT},
                                              "size": path.stat().st_size, "digest": "sha256:" + prepare.digest(path)})

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.hash_patch = patch.object(ffmpeg, "SOURCE_SHA256", self.source_hash)
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)
        self.identity = patch.object(prepare, "validate_identity", return_value=(self.version, self.code))
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def verify(self, metadata=None):
        return assets.verify_directory(self.base, self.tag, self.commit, metadata or self.metadata, self.provenance)

    def test_complete_two_platform_release_validates_without_native_execution(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("No native execution on publisher")):
            self.assertEqual(assets.public_names(self.version), self.verify())
        self.assertEqual(3, len(self.metadata["assets"]))
        self.assertEqual(18, len(assets.required_names(self.version)))

    def test_windows_internal_stage_retains_native_proof_and_checksum_sidecars(self):
        # Producer startup execution has separate native self-test tests. This
        # transport test uses explicitly synthetic evidence, never runs/repackages an app.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for name in assets.platform_names(self.version, "windows"):
                shutil.copyfile(self.base / name, output / name)
            evidence = assets.read_json(output / "windows-evidence.json")
            self.assertEqual("passed", evidence["native_verification"]["result"])
            self.assertEqual(self.code, evidence["code_sha256"])
            self.assertEqual(self.commit, evidence["release_commit"])
            for path in output.iterdir():
                if not path.name.endswith(".sha256"):
                    assets.checksum(path)
            assets.validate_evidence(evidence, "windows", self.tag, self.commit, self.version, self.code, output, self.provenance)
            assets.verify_windows_zip(output / prepare.artifact_names(self.version)["windows"][1], evidence)

    def test_prepare_failed_native_gate_writes_no_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "evidence"
            with patch.object(sys, "platform", "win32"), patch.object(prepare, "execute", side_effect=ValueError("native failed")), self.assertRaises(ValueError):
                prepare.prepare(self.tag, self.commit, "windows", root, root, root, output)
            self.assertFalse((output / "windows-evidence.json").exists())

    def test_missing_mac_evidence_and_published_release_fail(self):
        for change in ("missing", "published"):
            metadata = copy.deepcopy(self.metadata)
            if change == "missing":
                metadata["assets"] = [item for item in metadata["assets"] if item["name"] != prepare.artifact_names(self.version)["macos"][0]]
            else:
                metadata["draft"] = False
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.verify(metadata)

    def test_unauthorized_native_mac_or_windows_uploader_fails(self):
        for name in assets.public_names(self.version):
            metadata = copy.deepcopy(self.metadata)
            next(item for item in metadata["assets"] if item["name"] == name)["uploader"]["login"] = "untrusted"
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unauthorized"):
                self.verify(metadata)

    def test_stale_remote_asset_size_or_digest_fails(self):
        for key, value in (("size", 1), ("digest", "sha256:" + "0" * 64), ("digest", None), ("state", "new")):
            metadata = copy.deepcopy(self.metadata)
            metadata["assets"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(metadata)

    def test_missing_internal_proof_fails_even_with_three_public_assets(self):
        for name in ("mac-evidence.json", "windows-evidence.json", prepare.SOURCE_NAME):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / "verification"
                shutil.copytree(self.base, directory)
                (directory / name).unlink()
                with self.assertRaisesRegex(ValueError, "internal evidence"):
                    assets.verify_directory(directory, self.tag, self.commit, self.metadata, self.provenance)

    def test_native_json_cannot_self_authorize_actor_run_or_source(self):
        for key, value in (("actor", "forged"), ("run_id", "124"), ("run_attempt", "2"), ("source_commit", "b" * 40)):
            evidence = copy.deepcopy(self.evidence["macos"])
            evidence["workflow_provenance"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "provenance mismatch"):
                assets.validate_evidence(evidence, "macos", self.tag, self.commit, self.version, self.code, self.base, self.provenance)
        evidence = copy.deepcopy(self.evidence["macos"])
        evidence.pop("workflow_provenance")
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            assets.validate_evidence(evidence, "macos", self.tag, self.commit, self.version, self.code, self.base, self.provenance)

    def test_public_sidecar_or_extra_proof_asset_rejects(self):
        for name in ("mac-evidence.json", "SHA256SUMS", "artifact.dmg.sha256"):
            metadata = copy.deepcopy(self.metadata)
            metadata["assets"].append({"name": name, "state": "uploaded"})
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "draft assets"):
                self.verify(metadata)

    def test_frontend_startup_source_files_must_be_commit_hashed(self):
        for name in ("desktop_app.py", "sjtu_learning_assistant/frontend_startup.py", "sjtu_learning_assistant/frontend_self_test.py"):
            code = dict(self.code)
            code.pop(name)
            evidence = copy.deepcopy(self.evidence["macos"])
            evidence["code_sha256"] = code
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "source hashes missing"):
                assets.validate_evidence(evidence, "macos", self.tag, self.commit, self.version, code, self.base, self.provenance)

    def test_frontend_startup_report_is_strict_typed_sanitized_and_platform_bound(self):
        for platform_name in ("macos", "windows"):
            valid = startup_report(platform_name)
            assets.validate_frontend_startup(valid, platform_name)
            mutations = (
                ("schema_version", True), ("schema_version", 1.0), ("result", "failed"), ("reason", "/private/path"),
                ("renderer", "chromium"), ("backend", "real-service"), ("report_sha256", None),
                ("report_sha256", "A" * 64), ("frontend.sha256", "Z" * 64), ("frontend.sha256", 123),
                ("frontend.fingerprint", "wrong"), ("frontend.path", "/Users/private/index.html"),
                ("frontend.private_path", "/private/path"), ("private_path", "/private/path"),
                *((key, value) for key in ("app_shell", "bridge_ready", "data_isolated", "network_blocked")
                  for value in (False, 1, "true", None)),
            )
            for field, value in mutations:
                report = copy.deepcopy(valid)
                target, key = (report["frontend"], field.split(".", 1)[1]) if field.startswith("frontend.") else (report, field)
                target[key] = value
                with self.subTest(platform=platform_name, field=field, value=value), self.assertRaises(ValueError):
                    assets.validate_frontend_startup(report, platform_name)
            for field in valid:
                report = copy.deepcopy(valid)
                report.pop(field)
                with self.subTest(platform=platform_name, missing=field), self.assertRaises(ValueError):
                    assets.validate_frontend_startup(report, platform_name)

    def test_frontend_startup_requires_exact_native_executable_gate(self):
        for platform_name in ("macos", "windows"):
            for mutation in ("missing", "duplicate", "python-tool", "wrong-exe-tool", "failed", "bool-returncode", "private-field"):
                evidence = copy.deepcopy(self.evidence[platform_name])
                checks = evidence["native_verification"]["checks"]
                startup = next(item for item in checks if item["check"] == "frontend_startup")
                if mutation == "missing": checks.remove(startup)
                elif mutation == "duplicate": checks.append(copy.deepcopy(startup))
                elif mutation == "python-tool": startup["tool"] = "python.exe"
                elif mutation == "wrong-exe-tool": startup["tool"] = prepare.APP if platform_name == "windows" else prepare.APP + ".exe"
                elif mutation == "failed": startup["returncode"] = 1
                elif mutation == "private-field": startup["private_path"] = "/private/path"
                else: startup["returncode"] = False
                with self.subTest(platform=platform_name, mutation=mutation), self.assertRaises(ValueError):
                    assets.validate_evidence(evidence, platform_name, self.tag, self.commit, self.version, self.code, self.base, self.provenance)

    def test_identity_source_recipe_native_command_and_plist_tampering_fails(self):
        for mutation in ("commit", "version", "code", "source", "recipe", "license", "gpl", "dependency", "missing-command", "failed-command", "fake-tool", "plist", "dmg"):
            evidence = copy.deepcopy(self.evidence["macos"])
            manifest = evidence["ffmpeg"]["stage_manifest"]
            if mutation == "commit": evidence["release_commit"] = "b" * 40
            elif mutation == "version": evidence["app_version"] = "1.8.20"
            elif mutation == "code": evidence["code_sha256"] = {}
            elif mutation == "source": manifest["source_sha256"] = "0" * 64
            elif mutation == "recipe": manifest["recipe_sha256"] = {}
            elif mutation == "license": manifest["license"] = "GPL-3.0"
            elif mutation == "gpl": manifest["buildconf"].append("--enable-gpl")
            elif mutation == "dependency": manifest["system_dependencies"] = ["/opt/homebrew/lib/libx264.dylib"]
            elif mutation == "missing-command": evidence["native_verification"]["checks"].pop()
            elif mutation == "failed-command": evidence["native_verification"]["checks"][0]["returncode"] = 1
            elif mutation == "fake-tool": evidence["native_verification"]["checks"][3]["tool"] = "echo"
            elif mutation == "plist": evidence["application"]["plist"]["CFBundleVersion"] = "1.8.20"
            elif mutation == "dmg": evidence["application"]["dmg_matches_bundle"] = False
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, ffmpeg.VerificationError)):
                assets.validate_evidence(evidence, "macos", self.tag, self.commit, self.version, self.code, self.base)

    def test_wrong_checksum_filename_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.dmg"
            path.write_bytes(b"fixture")
            path.with_name(path.name + ".sha256").write_text(prepare.digest(path) + "  ../artifact.dmg\n")
            with self.assertRaisesRegex(ValueError, "Checksum/name"):
                assets.checksum(path)

    def test_zip_app_ffmpeg_manifest_vendor_and_traversal_tampering_fails(self):
        source = self.base / prepare.artifact_names(self.version)["windows"][1]
        for name, payload in ((prepare.APP + ".exe", b"changed app"), ("_internal/dashboard-web/dist/index.html", b"changed frontend"), ("_internal/ffmpeg/bin/ffmpeg.exe", b"changed ffmpeg"), ("_internal/ffmpeg/build-manifest.json", b"{}"), ("_internal/imageio_ffmpeg/binaries/ffmpeg.exe", b"vendor"), ("../escape", b"unsafe")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "portable.zip"
                with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as new:
                    for info in old.infolist():
                        new.writestr(info.filename, payload if info.filename == name else old.read(info))
                    if name not in old.namelist(): new.writestr(name, payload)
                with self.assertRaises((ValueError, ffmpeg.VerificationError)):
                    assets.verify_windows_zip(target, self.evidence["windows"])

    def test_source_only_or_renamed_setup_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Setup.exe"
            path.write_bytes(b"source-only; not a Windows executable")
            with self.assertRaisesRegex(ValueError, "not a PE executable"):
                assets.verify_installer(path)
            path.write_bytes(pe_bytes())
            assets.verify_installer(path)

    def test_zip_pe_imports_must_match_native_proof(self):
        source = self.base / prepare.artifact_names(self.version)["windows"][1]
        with patch.object(ffmpeg, "pe_imports", return_value={"libwinpthread-1.dll"}), self.assertRaisesRegex(ValueError, "PE imports"):
            assets.verify_windows_zip(source, self.evidence["windows"])


class ReleaseMetadataTests(unittest.TestCase):
    def draft(self):
        return {"id": 42, "tag_name": "v1.8.21", "draft": True,
                "html_url": "https://github.com/owner/repo/releases/tag/untagged-draft",
                "assets": [{"id": 7, "name": "mac-evidence.json", "state": "uploaded",
                            "size": 123, "uploader": {"login": "native-mac-owner"},
                            "digest": "sha256:" + "a" * 64}]}

    def test_tag_404_finds_paginated_exact_draft_preserving_rest_asset_facts(self):
        draft = self.draft()
        first_page = [{"tag_name": f"v0.0.{index}"} for index in range(100)]
        with patch.object(assets, "gh", side_effect=[None, json.dumps(first_page),
                json.dumps([{"tag_name": "v1.8.210", "draft": True}, draft])]) as gh:
            self.assertEqual(draft, assets.release_metadata("owner/repo", "v1.8.21"))
        self.assertEqual({"missing_ok": True}, gh.call_args_list[0].kwargs)
        self.assertEqual("repos/owner/repo/releases?per_page=100&page=2", gh.call_args.args[2])
        self.assertTrue(all("missing_ok" not in call.kwargs for call in gh.call_args_list[1:]))

    def test_initialize_reuses_draft_hidden_from_tag_endpoint_without_creating(self):
        draft = self.draft()
        with patch.object(prepare, "validate_identity"), patch.object(assets, "remote_commit", return_value="a" * 40), patch.object(assets, "gh", side_effect=[None, json.dumps([draft]), None, json.dumps([draft])]) as gh:
            self.assertTrue(assets.initialize("owner/repo", "v1.8.21", "a" * 40))
        self.assertEqual(4, gh.call_count)
        self.assertTrue(all(call.args[1] == "api" for call in gh.call_args_list))

    def test_tag_permission_error_is_not_absence_and_never_lists_or_creates(self):
        denied = subprocess.CompletedProcess([], 1, "", "gh: Resource not accessible (HTTP 403)")
        with patch.object(prepare, "validate_identity"), patch.object(assets, "remote_commit", return_value="a" * 40), patch.object(assets.subprocess, "run", return_value=denied) as run, self.assertRaisesRegex(ValueError, "HTTP 403"):
            assets.initialize("owner/repo", "v1.8.21", "a" * 40)
        self.assertEqual(1, run.call_count)
        self.assertIn("repos/owner/repo/releases/tags/v1.8.21", run.call_args.args[0])

    def test_fallback_list_failures_are_fatal_not_missing_or_duplicate_creation(self):
        not_found = subprocess.CompletedProcess([], 1, "", "gh: Not Found (HTTP 404)")
        for error in ("Resource not accessible (HTTP 403)", "Not Found (HTTP 404)", "connection timed out"):
            failed = subprocess.CompletedProcess([], 1, "", error)
            with self.subTest(error=error), patch.object(prepare, "validate_identity"), patch.object(assets, "remote_commit", return_value="a" * 40), patch.object(assets.subprocess, "run", side_effect=[not_found, failed]) as run, self.assertRaisesRegex(ValueError, "GitHub command failed"):
                assets.initialize("owner/repo", "v1.8.21", "a" * 40)
            self.assertEqual(2, run.call_count)
            self.assertTrue(all(call.args[0][1] == "api" for call in run.call_args_list))

    def test_complete_empty_listing_distinguishes_optional_from_required_lookup(self):
        for optional in (True, False):
            with self.subTest(optional=optional), patch.object(assets, "gh", side_effect=[None, "[]"]):
                if optional:
                    self.assertIsNone(assets.release_metadata("owner/repo", "v1.8.21", missing_ok=True))
                else:
                    with self.assertRaisesRegex(ValueError, "absent"):
                        assets.release_metadata("owner/repo", "v1.8.21")

    def test_listing_bound_is_failure_not_proof_of_absence(self):
        full_page = json.dumps([{"tag_name": f"v0.0.{index}"} for index in range(100)])
        with patch.object(assets, "gh", side_effect=[None] + [full_page] * 10) as gh, self.assertRaisesRegex(ValueError, "10-page lookup bound"):
            assets.release_metadata("owner/repo", "v1.8.21", missing_ok=True)
        self.assertEqual(11, gh.call_count)

    def test_direct_tag_success_does_not_list(self):
        draft = self.draft()
        with patch.object(assets, "gh", return_value=json.dumps(draft)) as gh:
            self.assertEqual(draft, assets.release_metadata("owner/repo", "v1.8.21"))
        self.assertEqual(1, gh.call_count)

    def test_invalid_or_ambiguous_listing_is_fatal_even_for_optional_lookup(self):
        for listing in ({"message": "denied"}, [self.draft(), self.draft()], [{"draft": True}]):
            with self.subTest(listing=listing), patch.object(assets, "gh", side_effect=[None, json.dumps(listing)]), self.assertRaises(ValueError):
                assets.release_metadata("owner/repo", "v1.8.21", missing_ok=True)


class DraftLifecycleTests(unittest.TestCase):
    def test_initialize_published_is_noop(self):
        with patch.object(prepare, "validate_identity"), patch.object(assets, "remote_commit", return_value="a" * 40), patch.object(assets, "release_metadata", return_value={"draft": False}), patch.object(assets, "gh") as gh:
            self.assertFalse(assets.initialize("owner/repo", "v1.8.21", "a" * 40))
        gh.assert_not_called()

    def test_initialize_absent_creates_only_draft(self):
        with patch.object(prepare, "validate_identity"), patch.object(assets, "remote_commit", return_value="a" * 40), patch.object(assets, "release_metadata", return_value=None), patch.object(assets, "assert_draft"), patch.object(assets, "gh") as gh:
            self.assertTrue(assets.initialize("owner/repo", "v1.8.21", "a" * 40))
        self.assertIn("--draft", gh.call_args.args)
        self.assertIn("--verify-tag", gh.call_args.args)
        self.assertNotIn("--latest", gh.call_args.args)

    def test_remote_moved_tag_blocks_mutation(self):
        with patch.object(assets, "remote_commit", return_value="b" * 40), patch.object(assets, "release_metadata") as metadata, self.assertRaisesRegex(ValueError, "Remote tag changed"):
            assets.assert_draft("owner/repo", "v1.8.21", "a" * 40)
        metadata.assert_not_called()

    def publisher_fixture(self):
        names = assets.public_names("1.8.21")
        return {"id": 42, "assets": [{"id": index, "name": name, "state": "uploaded", "size": 1,
                "digest": "sha256:" + "d" * 64} for index, name in enumerate(sorted(names))]}

    def fake_download(self, directory, order):
        def gh(*args, **kwargs):
            order.append(args)
            if args[1:3] == ("release", "download"):
                for name in assets.public_names("1.8.21"):
                    (Path(directory) / name).write_bytes(b"fixture")
        return gh

    def test_missing_or_extra_assets_never_promote(self):
        for change in ("missing", "extra", "duplicate", "incomplete"):
            metadata = self.publisher_fixture()
            if change == "missing": metadata["assets"].pop()
            elif change == "extra": metadata["assets"].append({"name": "mac-evidence.json", "state": "uploaded"})
            elif change == "duplicate": metadata["assets"][-1] = metadata["assets"][0]
            else: metadata["assets"][0]["state"] = "new"
            with self.subTest(change=change), patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(assets, "authenticate_native_run", return_value=({}, {})), patch.object(assets, "assert_draft", return_value=metadata), patch.object(assets, "gh") as gh, self.assertRaisesRegex(ValueError, "remains DRAFT"):
                assets.publish("owner/repo", "v1.8.21", "a" * 40, Path("unused"), "native-owner", "123", "1")
            gh.assert_not_called()

    def test_failed_validation_never_edits_release(self):
        order = []
        with tempfile.TemporaryDirectory() as directory, patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(assets, "authenticate_native_run", return_value=({}, {})), patch.object(assets, "assert_draft", return_value=self.publisher_fixture()), patch.object(assets, "gh", side_effect=self.fake_download(directory, order)), patch.object(assets, "merge_native_evidence"), patch.object(assets, "verify_directory", side_effect=ValueError("Invalid native proof")), self.assertRaises(ValueError):
            assets.publish("owner/repo", "v1.8.21", "a" * 40, Path(directory), "native-owner", "123", "1")
        self.assertTrue(all("edit" not in call for call in order))

    def test_missing_native_evidence_never_edits_release(self):
        with patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(assets, "authenticate_native_run", side_effect=ValueError("Missing native evidence")), patch.object(assets, "gh") as gh, self.assertRaises(ValueError):
            assets.publish("owner/repo", "v1.8.21", "a" * 40, Path("unused"), "native-owner", "123", "1")
        gh.assert_not_called()

    def test_success_promotes_only_after_both_platforms_validate(self):
        order = []
        with tempfile.TemporaryDirectory() as directory, patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(assets, "authenticate_native_run", return_value=({}, {})), patch.object(assets, "assert_draft", return_value=self.publisher_fixture()), patch.object(assets, "gh", side_effect=self.fake_download(directory, order)), patch.object(assets, "merge_native_evidence"), patch.object(assets, "verify_directory", side_effect=lambda *args: order.append("validated")):
            assets.publish("owner/repo", "v1.8.21", "a" * 40, Path(directory), "native-owner", "123", "1")
        self.assertEqual("validated", order[-2])
        self.assertIn("--draft=false", order[-1])
        self.assertIn("--notes-file", order[-1])
        self.assertIn("--latest", order[-1])

    def test_asset_replaced_during_validation_does_not_promote(self):
        metadata = self.publisher_fixture()
        replaced = copy.deepcopy(metadata)
        replaced["assets"][0]["id"] = 999
        order = []
        with tempfile.TemporaryDirectory() as directory, patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(assets, "authenticate_native_run", return_value=({}, {})), patch.object(assets, "assert_draft", side_effect=[metadata, replaced]), patch.object(assets, "gh", side_effect=self.fake_download(directory, order)), patch.object(assets, "merge_native_evidence"), patch.object(assets, "verify_directory"), self.assertRaisesRegex(ValueError, "changed during"):
            assets.publish("owner/repo", "v1.8.21", "a" * 40, Path(directory), "native-owner", "123", "1")
        self.assertTrue(all("edit" not in call for call in order))

    def test_native_artifact_changed_during_validation_does_not_promote(self):
        order = []
        with tempfile.TemporaryDirectory() as directory, patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(assets, "authenticate_native_run", side_effect=[({}, {}), ({}, {"macos": "replaced"})]), patch.object(assets, "assert_draft", return_value=self.publisher_fixture()), patch.object(assets, "gh", side_effect=self.fake_download(directory, order)), patch.object(assets, "merge_native_evidence"), patch.object(assets, "verify_directory"), self.assertRaisesRegex(ValueError, "changed during"):
            assets.publish("owner/repo", "v1.8.21", "a" * 40, Path(directory), "native-owner", "123", "1")
        self.assertTrue(all("edit" not in call for call in order))


class NativeArtifactTransportTests(unittest.TestCase):
    def responses(self):
        run = {"id": 123, "run_attempt": 1, "head_sha": "a" * 40, "path": ".github/workflows/release.yml",
               "event": "push", "status": "in_progress", "repository": {"full_name": "owner/repo", "id": 7},
               "head_repository": {"full_name": "owner/repo", "id": 7}, "actor": {"login": "native-owner"}}
        jobs = {"total_count": 5, "jobs": [
            {"name": name, "status": "completed", "conclusion": "success", "run_attempt": 1}
            for name in ("initialize", "release-checks", "windows-release", "macos-release")
        ] + [{"name": "publish-release", "status": "in_progress", "run_attempt": 1}]}
        listing = {"total_count": 2, "artifacts": [
            {"id": index + 10, "name": assets.native_artifact_name(platform_name, "123", "1"),
             "expired": False, "size_in_bytes": 100, "digest": "sha256:" + "d" * 64,
             "workflow_run": {"id": 123, "head_sha": "a" * 40, "repository_id": 7, "head_repository_id": 7}}
            for index, platform_name in enumerate(("macos", "windows"))]}
        return run, jobs, listing

    def authenticate(self, responses):
        with patch.object(assets, "gh", side_effect=[json.dumps(item) for item in responses]):
            return assets.authenticate_native_run("owner/repo", "a" * 40, "123", "1", "native-owner")

    def test_same_release_run_and_native_jobs_authenticate(self):
        provenance, artifacts = self.authenticate(self.responses())
        self.assertEqual("native-owner", provenance["actor"])
        self.assertEqual("a" * 40, provenance["source_commit"])
        self.assertEqual({"macos", "windows"}, set(artifacts))

    def test_run_source_actor_workflow_and_attempt_mismatch_reject(self):
        for key, value in (("id", 124), ("run_attempt", 2), ("head_sha", "b" * 40), ("path", ".github/workflows/ci.yml"),
                           ("event", "pull_request"), ("status", "queued"), ("actor", {"login": "forged"}),
                           ("repository", {"full_name": "fork/repo"}), ("head_repository", {"full_name": "fork/repo"})):
            responses = self.responses()
            responses[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.authenticate(responses)

    def test_both_native_and_check_jobs_must_succeed_this_attempt(self):
        for name in ("initialize", "release-checks", "windows-release", "macos-release"):
            for key, value in (("conclusion", "failure"), ("conclusion", "skipped"), ("status", "in_progress"), ("run_attempt", 0)):
                responses = self.responses()
                next(job for job in responses[1]["jobs"] if job["name"] == name)[key] = value
                with self.subTest(name=name, key=key, value=value), self.assertRaises(ValueError):
                    self.authenticate(responses)
        responses = self.responses()
        responses[1]["jobs"].pop(3)
        responses[1]["total_count"] -= 1
        with self.assertRaises(ValueError): self.authenticate(responses)

    def test_missing_duplicate_expired_wrong_digest_or_source_artifact_rejects(self):
        for mutation in ("missing", "duplicate", "expired", "digest", "source", "run", "repository", "name"):
            responses = self.responses()
            listing = responses[2]
            item = listing["artifacts"][0]
            if mutation == "missing": listing["artifacts"].pop()
            elif mutation == "duplicate": listing["artifacts"].append(copy.deepcopy(item))
            elif mutation == "expired": item["expired"] = True
            elif mutation == "digest": item["digest"] = None
            elif mutation == "source": item["workflow_run"]["head_sha"] = "b" * 40
            elif mutation == "run": item["workflow_run"]["id"] = 124
            elif mutation == "repository": item["workflow_run"]["head_repository_id"] = 8
            else: item["name"] = assets.native_artifact_name("macos", "123", "2")
            listing["total_count"] = len(listing["artifacts"])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.authenticate(responses)

    def test_absent_pinned_run_or_actor_rejects_before_rest(self):
        for run_id, attempt, actor in ((None, "1", "owner"), ("123", None, "owner"), ("123", "1", None), ("123/x", "1", "owner")):
            with self.subTest(run_id=run_id, attempt=attempt, actor=actor), patch.object(assets, "gh") as gh, self.assertRaises(ValueError):
                assets.authenticate_native_run("owner/repo", "a" * 40, run_id, attempt, actor)
            gh.assert_not_called()

    def archive_bytes(self, names):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in names: archive.writestr(name, b"fixture")
        return stream.getvalue()

    def test_actions_archive_digest_and_exact_flat_file_set_are_mandatory(self):
        expected = assets.platform_names("1.8.21", "macos")
        for mutation in ("valid", "digest", "missing", "extra", "traversal", "duplicate"):
            names = sorted(expected)
            if mutation == "missing": names.pop()
            elif mutation == "extra": names.append("unrequested.txt")
            elif mutation == "traversal": names[-1] = "../outside"
            elif mutation == "duplicate": names.append(names[0])
            payload = self.archive_bytes(names)
            item = {"id": 10, "digest": "sha256:" + ("0" * 64 if mutation == "digest" else assets.sha_bytes(payload))}
            def run(*args, **kwargs):
                kwargs["stdout"].write(payload)
                return subprocess.CompletedProcess(args, 0, b"", b"")
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary, patch.object(assets.subprocess, "run", side_effect=run):
                destination = Path(temporary) / "proof"
                if mutation == "valid":
                    assets.download_native_artifact("owner/repo", item, destination, expected)
                    self.assertEqual(expected, {path.name for path in destination.iterdir()})
                else:
                    with self.assertRaises(ValueError): assets.download_native_artifact("owner/repo", item, destination, expected)
                    self.assertFalse(destination.exists())

    def test_shared_sources_must_be_byte_identical_between_native_artifacts(self):
        for changed in (False, True):
            def download(repo, item, destination, names):
                destination.mkdir()
                for name in names:
                    payload = b"changed" if changed and item == "windows" and name == prepare.SOURCE_NAME else b"fixture"
                    (destination / name).write_bytes(payload)
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary, patch.object(assets, "download_native_artifact", side_effect=download):
                directory = Path(temporary)
                if changed:
                    with self.assertRaisesRegex(ValueError, "shared evidence differs"):
                        assets.merge_native_evidence("owner/repo", "1.8.21", directory, {"macos": "macos", "windows": "windows"})
                else:
                    assets.merge_native_evidence("owner/repo", "1.8.21", directory, {"macos": "macos", "windows": "windows"})
                    self.assertEqual(assets.required_names("1.8.21"), {path.name for path in directory.iterdir()})

    def test_downloaded_release_bytes_cannot_be_overwritten_by_native_copies(self):
        def download(repo, item, destination, names):
            destination.mkdir()
            for name in names: (destination / name).write_bytes(b"native")
        with tempfile.TemporaryDirectory() as temporary, patch.object(assets, "download_native_artifact", side_effect=download):
            directory = Path(temporary)
            binary = directory / prepare.artifact_names("1.8.21")["macos"][0]
            binary.write_bytes(b"tampered remote")
            with self.assertRaisesRegex(ValueError, "differs from native bytes"):
                assets.merge_native_evidence("owner/repo", "1.8.21", directory, {"macos": "macos", "windows": "windows"})
            self.assertEqual(b"tampered remote", binary.read_bytes())

    def test_upload_rejects_extra_staging_or_existing_release_files(self):
        for mutation in ("staging-extra", "release-extra", "release-duplicate"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                for name in assets.platform_names("1.8.21", "macos"): (directory / name).write_bytes(b"fixture")
                metadata = {"assets": []}
                if mutation == "staging-extra": (directory / "evidence-not-requested.txt").write_bytes(b"fixture")
                elif mutation == "release-extra": metadata["assets"] = [{"name": "mac-evidence.json"}]
                else:
                    name = prepare.artifact_names("1.8.21")["macos"][0]
                    metadata["assets"] = [{"name": name}, {"name": name}]
                with patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(assets, "assert_draft", return_value=metadata), patch.object(assets, "gh") as gh, self.assertRaises(ValueError):
                    assets.upload("owner/repo", "v1.8.21", "a" * 40, directory)
                gh.assert_not_called()

    def test_upload_whitelist_is_only_dmg_setup_and_portable(self):
        uploaded = []
        for platform_name in ("macos", "windows"):
            with tempfile.TemporaryDirectory() as temporary, patch.object(prepare, "validate_identity", return_value=("1.8.21", {})), patch.object(assets, "assert_draft", return_value={"assets": []}), patch.object(assets, "gh", side_effect=lambda *args, **kwargs: uploaded.append(Path(args[-1]).name)):
                directory = Path(temporary)
                for name in assets.platform_names("1.8.21", platform_name): (directory / name).write_bytes(b"fixture")
                assets.upload("owner/repo", "v1.8.21", "a" * 40, directory)
        self.assertEqual(3, len(uploaded))
        self.assertEqual(assets.public_names("1.8.21"), set(uploaded))


class ReleaseWorkflowTests(unittest.TestCase):
    def test_three_digest_only_assets_work_without_public_sidecar_requests(self):
        import httpx
        from sjtu_learning_assistant.update_service import UpdateService
        prefix = "https://github.com/Augety-silence/sjtu-learning-assistant/releases/"
        names = assets.public_names("1.8.24")
        payload = {"tag_name": "v1.8.24", "draft": False, "prerelease": False,
                   "html_url": prefix + "tag/v1.8.24", "body": "verified native build", "assets": [
                       {"name": name, "size": 1, "digest": "sha256:" + "d" * 64,
                        "browser_download_url": prefix + "download/v1.8.24/" + name} for name in sorted(names)]}
        for system, machine, requested, expected in (
            ("Windows", "x64", None, prepare.artifact_names("1.8.24")["windows"][0]),
            ("Darwin", "arm64", None, prepare.artifact_names("1.8.24")["macos"][0]),
            ("Windows", "x64", prepare.artifact_names("1.8.24")["windows"][1], prepare.artifact_names("1.8.24")["windows"][1]),
        ):
            seen = []
            def handler(request):
                seen.append(str(request.url))
                self.assertEqual("api.github.com", request.url.host)
                return httpx.Response(200, json=payload)
            with self.subTest(system=system, requested=requested):
                check = UpdateService("1.8.23", transport=httpx.MockTransport(handler)).check(
                    system=system, machine=machine, asset_name=requested)
                self.assertEqual(expected, check.download_plan.asset_name)
                self.assertEqual("d" * 64, check.download_plan.sha256)
                self.assertIsNone(check.download_plan.checksum_url)
                self.assertEqual(1, len(seen))

    def test_native_mac_default_draft_gate_and_windows_independence(self):
        text = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("default: false", text)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.use_local_macos", text)
        self.assertIn('test "$USE_LOCAL_MACOS" = "false"', text)
        self.assertNotIn("FFMPEG_BINARY_REDISTRIBUTION_CLEARED", text)
        self.assertIn("msys2/setup-msys2@v2", text)
        self.assertIn("msystem: MINGW64", text)
        self.assertIn("mingw-w64-x86_64-gcc make", text)
        self.assertIn("SJTU_MSYS2_BASH", text)
        windows = text.split("  windows-release:\n", 1)[1].split("  macos-release:\n", 1)[0]
        self.assertNotIn("needs: macos-release", windows)
        self.assertIn("python.exe -m pytest", windows)
        self.assertNotIn("needs.initialize.outputs.use_local_macos == 'false'", text)
        self.assertIn("always() &&", text)
        self.assertIn("needs.windows-release.result == 'success'", text)
        self.assertIn("needs.macos-release.result == 'success'", text)
        self.assertNotIn("use_local_macos == 'true' ||", text)
        self.assertEqual(2, text.count("uses: actions/upload-artifact@v4"))
        self.assertIn('--release-run "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT" --actor "$RELEASE_ACTOR"', text)
        self.assertIn("actions: read", text)
        self.assertIn("22.22.0", text)
        self.assertIn("10.9.4", text)
        self.assertIn("check_licenses.py", text)
        self.assertIn("scan_secrets.py", text)


if __name__ == "__main__":
    unittest.main()
