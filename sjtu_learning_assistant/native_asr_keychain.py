"""macOS ASR 凭据原位更新，保存不先删除已有有效密钥。"""
from __future__ import annotations


class NativeKeychainError(RuntimeError):
    def __init__(self, status: int) -> None:
        self.status = int(status)
        super().__init__("macOS Keychain operation failed")


class NativeASRKeychain:
    def __init__(self, security=None, *, allow_interaction: bool = True):
        if security is None:
            import Security
            security = Security
        self.security = security
        self.allow_interaction = allow_interaction

    def _query(self, service: str, account: str) -> dict:
        native = self.security
        query = {native.kSecClass: native.kSecClassGenericPassword,
                 native.kSecAttrService: service, native.kSecAttrAccount: account}
        if not self.allow_interaction:
            query[native.kSecUseAuthenticationUI] = native.kSecUseAuthenticationUIFail
        return query

    @staticmethod
    def _check(status: int) -> None:
        if status:
            raise NativeKeychainError(status)

    def set_password(self, service: str, account: str, value: str) -> None:
        native = self.security
        query = self._query(service, account)
        change = {native.kSecValueData: value.encode("utf-8")}
        status = native.SecItemUpdate(query, change)
        if status == -25300:
            status, _ = native.SecItemAdd({**query, **change}, None)
            if status == -25299:
                status = native.SecItemUpdate(query, change)
        self._check(status)

    def get_password(self, service: str, account: str) -> str | None:
        native = self.security
        query = {**self._query(service, account), native.kSecReturnData: True,
                 native.kSecMatchLimit: native.kSecMatchLimitOne}
        status, data = native.SecItemCopyMatching(query, None)
        if status == -25300:
            return None
        self._check(status)
        return bytes(data).decode("utf-8")

    def delete_password(self, service: str, account: str) -> None:
        status = self.security.SecItemDelete(self._query(service, account))
        if status != -25300:
            self._check(status)
