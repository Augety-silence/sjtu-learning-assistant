"""PyInstaller hook for the native Windows Credential Manager backend (WinVaultKeyring)."""

from PyInstaller.utils.hooks import copy_metadata


datas = copy_metadata("keyring")
hiddenimports = [
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.Windows",
    "win32ctypes.core",
    "win32ctypes.pywin32.pywintypes",
    "win32ctypes.pywin32.win32cred",
]
excludedimports = [
    "keyring.backends.KWallet",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.libsecret",
    "keyring.backends.macOS",
    "keyring.testing",
]
