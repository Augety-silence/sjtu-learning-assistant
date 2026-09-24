# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from sjtu_learning_assistant import __version__

ROOT = Path(SPECPATH).parent
APP_NAME = "SJTU Learning Assistant"
ICON = ROOT / "packaging" / "app.ico"

datas = [
    (str(ROOT / "dashboard-web" / "dist"), "dashboard-web/dist"),
    (str(ROOT / "migrations"), "migrations"),
    (str(ROOT / "LICENSE"), "licenses"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "licenses"),
    (str(ROOT / "AGENT.md"), "."),
    (str(ROOT / "agent_presets"), "agent_presets"),
]

hiddenimports = [
    "logging.config",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.Windows",
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
]

analysis = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "windows-hooks")],
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
    icon=str(ICON),
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
