"""原位更新不能先删除有效密钥；错误码保持脱敏。"""
from unittest.mock import Mock, patch
import pytest

from sjtu_learning_assistant.asr_service import ASRError, save_asr_api_key
from sjtu_learning_assistant.native_asr_keychain import NativeASRKeychain, NativeKeychainError


def security_mock():
    api = Mock()
    for name in ("kSecClass", "kSecClassGenericPassword", "kSecAttrService", "kSecAttrAccount", "kSecValueData", "kSecReturnData", "kSecMatchLimit", "kSecMatchLimitOne"):
        setattr(api, name, name)
    api.SecItemUpdate.return_value = 0
    api.SecItemAdd.return_value = (0, None)
    api.SecItemCopyMatching.return_value = (0, b"synthetic")
    api.SecItemDelete.return_value = 0
    return api


def test_save_existing_uses_update_not_delete_add():
    api = security_mock()
    NativeASRKeychain(api).set_password("asr-service", "account", "synthetic")
    api.SecItemUpdate.assert_called_once()
    api.SecItemDelete.assert_not_called()
    api.SecItemAdd.assert_not_called()
    assert api.SecItemUpdate.call_args.args[0]["kSecAttrService"] == "asr-service"


def test_create_only_if_missing_and_race_reupdates():
    api = security_mock()
    api.SecItemUpdate.side_effect = [-25300, 0]
    api.SecItemAdd.return_value = (-25299, None)
    NativeASRKeychain(api).set_password("asr-service", "account", "synthetic")
    assert api.SecItemUpdate.call_count == 2
    api.SecItemDelete.assert_not_called()


@pytest.mark.parametrize("status", [-128, -25293, -25308, -67030])
def test_permission_denied_preserves_existing_and_reports_code(status, caplog):
    api = security_mock()
    api.SecItemUpdate.return_value = status
    with pytest.raises(ASRError) as error:
        save_asr_api_key("synthetic", keyring_module=NativeASRKeychain(api))
    assert str(status) in str(error.value)
    assert "synthetic" not in str(error.value) and "synthetic" not in caplog.text
    api.SecItemAdd.assert_not_called()
    api.SecItemDelete.assert_not_called()


def test_read_missing_and_delete_missing_are_idempotent():
    api = security_mock()
    api.SecItemCopyMatching.return_value = (-25300, None)
    api.SecItemDelete.return_value = -25300
    store = NativeASRKeychain(api)
    assert store.get_password("s", "a") is None
    store.delete_password("s", "a")


def test_saved_key_must_roundtrip_before_success():
    api = security_mock()
    api.SecItemCopyMatching.return_value = (0, b"different")
    with pytest.raises(ASRError, match="回读校验失败"):
        save_asr_api_key("synthetic", keyring_module=NativeASRKeychain(api))


def test_backend_unavailable_exception_never_leaks_values(caplog):
    backend = Mock()
    backend.set_password.side_effect = RuntimeError("secret-server-string")
    with pytest.raises(ASRError) as error:
        save_asr_api_key("synthetic", keyring_module=backend)
    assert "secret-server-string" not in str(error.value) + caplog.text


def test_trust_is_only_for_asr_decrypt_acl_not_other_credentials():
    from scripts.asr_keychain_access_local import run, BINARY
    api = security_mock()
    api.kSecReturnRef = "return-ref"
    api.kSecUseAuthenticationUI = "auth-ui"
    api.kSecUseAuthenticationUIFail = "no-ui"
    api.kSecACLAuthorizationDecrypt = "decrypt"
    api.SecItemCopyMatching.return_value = (0, "item")
    api.SecKeychainItemCopyAccess.return_value = (0, "access")
    api.SecAccessCopyACLList.return_value = (0, ["decrypt-acl", "partition-acl"])
    api.SecACLCopyAuthorizations.side_effect = lambda acl: ["decrypt"] if acl == "decrypt-acl" else ["partition"]
    api.SecACLCopyContents.return_value = (0, ["existing-python"], "description", 1)
    api.SecTrustedApplicationCopyData.return_value = (0, b"existing-python")
    api.SecTrustedApplicationCreateFromPath.return_value = (0, "installed-app")
    api.SecACLSetContents.return_value = 0
    api.SecKeychainItemSetAccess.return_value = 0
    result = run("trust", api)
    assert result["changed"]
    query = api.SecItemCopyMatching.call_args.args[0]
    assert query["kSecAttrService"] == "SJTU Learning Assistant - Speech Recognition"
    assert "kSecReturnData" not in query
    api.SecACLSetContents.assert_called_once_with("decrypt-acl", ["existing-python", "installed-app"], "description", 1)
    api.SecTrustedApplicationCreateFromPath.assert_called_once_with(str(BINARY).encode(), None)
    api.SecItemDelete.assert_not_called()
    api.SecItemAdd.assert_not_called()


def test_native_probe_no_interaction_is_explicit():
    api = security_mock()
    api.kSecUseAuthenticationUI = "auth-ui"
    api.kSecUseAuthenticationUIFail = "no-ui"
    NativeASRKeychain(api, allow_interaction=False).get_password("s", "a")
    assert api.SecItemCopyMatching.call_args.args[0]["auth-ui"] == "no-ui"
