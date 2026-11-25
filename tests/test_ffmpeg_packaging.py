from __future__ import annotations

import importlib.util
import os
import runpy
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sjtu_learning_assistant import media_features

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ffmpeg_verifier", ROOT / "scripts" / "verify_ffmpeg.py")
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class FrozenFfmpegTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def binary(self, platform: str) -> Path:
        path = self.root / "ffmpeg" / "bin" / ("ffmpeg.exe" if platform == "win32" else "ffmpeg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test fixture, not real FFmpeg")
        path.chmod(0o755)
        return path

    def test_frozen_resolver_ignores_path_and_environment(self) -> None:
        for platform in ("darwin", "win32"):
            binary = self.binary(platform)
            with self.subTest(platform=platform), patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(self.root), create=True), patch.object(sys, "platform", platform), patch.object(media_features.shutil, "which", side_effect=AssertionError("must not use PATH")), patch.dict(os.environ, {"IMAGEIO_FFMPEG_EXE": "/untrusted/vendor"}):
                self.assertEqual(str(binary), media_features._ffmpeg_executable())

    def test_missing_frozen_binary_fails_closed(self) -> None:
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(self.root), create=True), patch.object(media_features.shutil, "which", side_effect=AssertionError("must not use PATH")):
            with self.assertRaisesRegex(media_features.MediaFeatureError, "Packaged FFmpeg"):
                media_features._ffmpeg_executable()

    def test_missing_meipass_fails_closed(self) -> None:
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", None, create=True):
            with self.assertRaisesRegex(media_features.MediaFeatureError, "Packaged FFmpeg"):
                media_features._ffmpeg_executable()

    def test_runtime_hook_overrides_user_selected_wrapper_executable(self) -> None:
        for platform in ("darwin", "win32"):
            binary = self.binary(platform)
            with self.subTest(platform=platform), patch.object(sys, "_MEIPASS", str(self.root), create=True), patch.object(sys, "platform", platform), patch.dict(os.environ, {"IMAGEIO_FFMPEG_EXE": "/untrusted/vendor"}):
                runpy.run_path(str(ROOT / "packaging" / "runtime_hooks" / "ffmpeg.py"))
                self.assertEqual(str(binary), os.environ["IMAGEIO_FFMPEG_EXE"])

    def test_runtime_hook_missing_binary_fails_before_imports(self) -> None:
        with patch.object(sys, "_MEIPASS", str(self.root), create=True):
            with self.assertRaisesRegex(RuntimeError, "Packaged FFmpeg"):
                runpy.run_path(str(ROOT / "packaging" / "runtime_hooks" / "ffmpeg.py"))

    def test_development_still_supports_path(self) -> None:
        with patch.object(sys, "frozen", False, create=True), patch.object(media_features.shutil, "which", return_value="/development/ffmpeg"):
            self.assertEqual("/development/ffmpeg", media_features._ffmpeg_executable())


class FfmpegBuildGuards(unittest.TestCase):
    def test_specs_require_stage_and_override_vendor_hook(self) -> None:
        for relative in ("packaging/desktop.spec", "packaging/windows.spec"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(spec=relative):
                self.assertIn("SJTU_FFMPEG_STAGE", text)
                self.assertIn('scripts" / "verify_ffmpeg.py', text)
                self.assertIn('"common-hooks"', text)
                self.assertIn('"runtime_hooks" / "ffmpeg.py"', text)
                self.assertIn('"ffmpeg/bin"', text)
                self.assertNotIn('collect_data_files("imageio_ffmpeg")', text)
                self.assertNotIn('"imageio_ffmpeg.binaries"', text)
                self.assertLess(text.index("verify_ffmpeg.py"), text.index("from sjtu_learning_assistant import __version__"))
        hook = runpy.run_path(str(ROOT / "packaging/common-hooks/hook-imageio_ffmpeg.py"))
        self.assertEqual([], hook["datas"])
        self.assertEqual([], hook["binaries"])
        self.assertIn("imageio_ffmpeg.binaries", hook["excludedimports"])

    def test_builders_verify_stage_and_bundle(self) -> None:
        for relative in ("scripts/build_macos_app.sh", "scripts/build_windows_app.ps1"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("SJTU_FFMPEG_STAGE", text)
            self.assertIn("SJTU_FFMPEG_SKIP_BUILD", text)
            self.assertIn("build_ffmpeg.sh", text)
            self.assertIn("verify_ffmpeg.py", text)
            self.assertIn("--bundle", text)
        windows = (ROOT / "scripts/build_windows_app.ps1").read_text(encoding="utf-8")
        self.assertNotIn("_internal\\imageio_ffmpeg\\binaries", windows)

    def test_configuration_rejects_gpl_external_libs_and_disabled_core(self) -> None:
        flags = " ".join(sorted(verifier.REQUIRED_FLAGS))
        license_text = "GNU Lesser General Public License version 2.1"
        for extra in ("--enable-gpl", "--enable-nonfree", "--enable-version3", "--enable-libx264", "--disable-everything", "--disable-decoders", "--disable-filters"):
            with self.subTest(extra=extra), self.assertRaises(verifier.VerificationError):
                verifier.verify_license_and_flags(license_text, "configuration: " + flags + " " + extra, "ffmpeg version 7.1.3\n")
        verifier.verify_license_and_flags(license_text, "configuration: " + flags, "ffmpeg version 7.1.3\n")
        with self.assertRaises(verifier.VerificationError):
            verifier.verify_license_and_flags("GNU General Public License version 3", "configuration: " + flags, "ffmpeg version 7.1.3\n")
        with self.assertRaises(verifier.VerificationError):
            verifier.verify_license_and_flags(license_text, "configuration: " + flags, "ffmpeg version 7.1.2\n")

    def test_vendor_binary_leak_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            leaked = root / "_internal/imageio_ffmpeg/binaries/ffmpeg-vendor.exe"
            leaked.parent.mkdir(parents=True)
            leaked.write_bytes(b"fixture")
            with self.assertRaisesRegex(verifier.VerificationError, "vendor data"):
                verifier.verify_no_vendor_binaries(root)

    def test_missing_or_modified_source_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(verifier.VerificationError, "source archive"):
                verifier.verify_source(root)
            archive = root / "source/FFmpeg-n7.1.3.tar.gz"
            archive.parent.mkdir()
            archive.write_bytes(b"not the pinned source")
            with self.assertRaisesRegex(verifier.VerificationError, "source archive"):
                verifier.verify_source(root)

    def test_pe_import_reader_and_non_system_dll_gate(self) -> None:
        # Minimal PE32+ containing one import descriptor; no executable code.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ffmpeg.exe"
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
            name = b"kernel32.dll\0"
            data[640:640 + len(name)] = name
            path.write_bytes(data)
            self.assertEqual({"kernel32.dll"}, verifier.pe_imports(path))
            with patch.object(sys, "platform", "win32"):
                self.assertEqual(["kernel32.dll"], verifier.verify_dependencies(path))
                with patch.object(verifier, "pe_imports", return_value={"libwinpthread-1.dll"}), self.assertRaisesRegex(verifier.VerificationError, "non-system DLLs"):
                    verifier.verify_dependencies(path)


if __name__ == "__main__":
    unittest.main()

class RealLicenseOutputTests(unittest.TestCase):
    def test_wrapped_lgpl_and_disabled_flags_are_parsed(self):
        output = "GNU Lesser General Public\nLicense version 2.1 or later\nffmpeg version 7.1.3\nconfiguration: --disable-nonfree"
        configuration = "configuration: " + " ".join(sorted(verifier.REQUIRED_FLAGS))
        self.assertTrue(verifier.verify_license_and_flags(output, configuration, "ffmpeg version 7.1.3\n"))

class WindowsSystemDllInventoryTests(unittest.TestCase):
    def test_avicap_is_a_system_dependency_not_a_vendor_runtime(self):
        self.assertIn("avicap32.dll", verifier.SYSTEM_DLLS)
        self.assertNotIn("libgcc_s_seh-1.dll", verifier.SYSTEM_DLLS)
        self.assertNotIn("libwinpthread-1.dll", verifier.SYSTEM_DLLS)
