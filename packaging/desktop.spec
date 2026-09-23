# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.building.osx import BUNDLE
from PyInstaller.utils import osx as pyinstaller_osx

from sjtu_learning_assistant import __version__

ROOT = Path(SPECPATH).parent
APP_NAME = "SJTU Learning Assistant"


class PostSignedBundle(BUNDLE):
    """Let the build script clean bundle-local xattrs before ad-hoc signing."""

    def assemble(self):
        original_sign_binary = pyinstaller_osx.sign_binary
        bundle_path = Path(self.name).resolve()

        def defer_bundle_signing(filename, identity=None, entitlements_file=None, deep=False):
            if Path(filename).resolve() == bundle_path:
                return None
            return original_sign_binary(filename, identity, entitlements_file, deep)

        pyinstaller_osx.sign_binary = defer_bundle_signing
        try:
            return super().assemble()
        finally:
            pyinstaller_osx.sign_binary = original_sign_binary


datas = [
    (str(ROOT / "dashboard-web" / "dist"), "dashboard-web/dist"),
    (str(ROOT / "LICENSE"), "licenses"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "licenses"),
]

hiddenimports = [
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.macOS",
    "keyring.backends.macOS.api",
    "sqlalchemy.dialects.postgresql",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "sqlalchemy.ext.baked",
    "sqlalchemy.sql.default_comparator",
    "webview.platforms.cocoa",
]

excludes = [
    "asyncpg",
    "fastapi",
    "psycopg",
    "psycopg2",
    "psycopg_binary",
    "psycopg_c",
    "uvicorn",
    "sqlalchemy.testing",
    "keyring.testing",
    "keyring.backends.KWallet",
    "keyring.backends.SecretService",
    "keyring.backends.Windows",
    "keyring.backends.kwallet",
    "keyring.backends.libsecret",
    "webview.platforms.android",
    "webview.platforms.cef",
    "webview.platforms.edgechromium",
    "webview.platforms.gtk",
    "webview.platforms.mshtml",
    "webview.platforms.qt",
    "webview.platforms.winforms",
]

analysis = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
app = PostSignedBundle(
    collection,
    name=f"{APP_NAME}.app",
    icon=str(ROOT / "packaging" / "app.icns"),
    bundle_identifier="io.github.sjtu-learning-assistant",
    version=__version__,
    info_plist={
        "CFBundleDisplayName": APP_NAME,
        "CFBundleName": APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Copyright 2026 周济睿",
    },
)
