"""PyInstaller hook limited to the native macOS Keychain backend."""

from PyInstaller.utils.hooks import copy_metadata


datas = copy_metadata("keyring")
hiddenimports = [
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.macOS",
    "keyring.backends.macOS.api",
]
excludedimports = [
    "keyring.backends.KWallet",
    "keyring.backends.SecretService",
    "keyring.backends.Windows",
    "keyring.backends.kwallet",
    "keyring.backends.libsecret",
    "keyring.testing",
]
