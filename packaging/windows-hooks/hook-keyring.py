"""PyInstaller hook for the native Windows Credential Locker backend."""

from PyInstaller.utils.hooks import copy_metadata


datas = copy_metadata("keyring")
hiddenimports = [
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.Windows",
]
excludedimports = [
    "keyring.backends.KWallet",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.libsecret",
    "keyring.backends.macOS",
    "keyring.testing",
]
