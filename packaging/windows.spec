# -*- mode: python ; coding: utf-8 -*-
import os
import subprocess
import sys
from pathlib import Path

# Fail closed before importing application code or running PyInstaller analysis.
ROOT = Path(SPECPATH).parent
FFMPEG_STAGE = Path(os.environ.get("SJTU_FFMPEG_STAGE", str(ROOT / "dist" / "ffmpeg"))).resolve()
subprocess.run(
    (sys.executable, str(ROOT / "scripts" / "verify_ffmpeg.py"), "--stage", str(FFMPEG_STAGE)),
    check=True,
)

from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct

from sjtu_learning_assistant import __version__

APP_NAME = "SJTU Learning Assistant"
ICON = ROOT / "packaging" / "app.ico"

datas = [
    (str(FFMPEG_STAGE / "licenses"), "ffmpeg/licenses"),
    (str(FFMPEG_STAGE / "source"), "ffmpeg/source"),
    (str(FFMPEG_STAGE / "build-manifest.json"), "ffmpeg"),
    (str(ROOT / "dashboard-web" / "dist"), "dashboard-web/dist"),
    (str(ROOT / "resources" / "seed_glossary"), "resources/seed_glossary"),
    (str(ROOT / "migrations"), "migrations"),
    (str(ROOT / "resources" / "samples"), "resources/samples"),
    (str(ROOT / "LICENSE"), "licenses"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "licenses"),
    (str(ROOT / "AGENT.md"), "."),
    (str(ROOT / "agent_presets"), "agent_presets"),
    *collect_data_files("pypdfium2"),
    *copy_metadata("pypdfium2"),
    *collect_data_files("tzdata"),
    *copy_metadata("mcp"),
    *copy_metadata("PyYAML"),
]

hiddenimports = [
    "logging.config",
    "imageio_ffmpeg",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.Windows",
    "pypdf",
    "pypdfium2",
    "yaml",
    "mcp.client.stdio",
    "mcp.client.sse",
    "mcp.client.streamable_http",
    "sqlalchemy.dialects.postgresql",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "sqlalchemy.ext.baked",
    "sqlalchemy.sql.default_comparator",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
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
    "keyring.backends.kwallet",
    "keyring.backends.libsecret",
    "keyring.backends.macOS",
    "webview.platforms.android",
    "webview.platforms.cef",
    "webview.platforms.cocoa",
    "webview.platforms.gtk",
    "webview.platforms.mshtml",
    "webview.platforms.qt",
    "EventKit",
    "Foundation",
    "AppKit",
    "objc",
]

analysis = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=[(str(FFMPEG_STAGE / "bin" / "ffmpeg.exe"), "ffmpeg/bin")],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "common-hooks"), str(ROOT / "packaging" / "windows-hooks")],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "runtime_hooks" / "ffmpeg.py")],
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
    icon=str(ICON),
    version=VSVersionInfo(
        ffi=FixedFileInfo(filevers=tuple(int(part) for part in __version__.split(".")) + (0,),
                          prodvers=tuple(int(part) for part in __version__.split(".")) + (0,),
                          mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
        kids=[StringFileInfo([StringTable("040904B0", [StringStruct("FileVersion", __version__), StringStruct("ProductVersion", __version__), StringStruct("ProductName", APP_NAME)])]),
              VarFileInfo([VarStruct("Translation", [1033,1200])])]),
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
