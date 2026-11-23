"""仅为既有 ASR 密钥添加已安装 App 的读取许可，不读取或输出密钥。

inspect 为只读；trust 只修改这一条密钥的解密 ACL，保留现有应用及其他权限。
"""
import argparse
import json
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.asr_service import ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT

APP = Path("/Applications/SJTU Learning Assistant.app")
BINARY = APP / "Contents/MacOS/SJTU Learning Assistant"


def check(status):
    if status:
        raise RuntimeError(f"Keychain {status}；未扩大其他权限，请在钥匙串授权窗口允许本应用访问。")


def run(action, security):
    security.SecKeychainSetUserInteractionAllowed(False)
    try:
        query = {security.kSecClass: security.kSecClassGenericPassword,
                 security.kSecAttrService: ASR_KEYCHAIN_SERVICE,
                 security.kSecAttrAccount: ASR_KEYCHAIN_ACCOUNT,
                 security.kSecReturnRef: True,
                 security.kSecUseAuthenticationUI: security.kSecUseAuthenticationUIFail}
        status, item = security.SecItemCopyMatching(query, None)
        check(status)
        status, access = security.SecKeychainItemCopyAccess(item, None)
        check(status)
        status, acls = security.SecAccessCopyACLList(access, None)
        check(status)
        found, changed = False, False
        for acl in acls:
            authorizations = security.SecACLCopyAuthorizations(acl)
            if security.kSecACLAuthorizationDecrypt not in authorizations:
                continue
            status, applications, description, selector = security.SecACLCopyContents(acl, None, None, None)
            check(status)
            if applications is None:
                found = True  # 已有的“所有应用”策略，不擅自更改。
                continue
            for application in applications:
                status, data = security.SecTrustedApplicationCopyData(application, None)
                check(status)
                matches_path = str(BINARY).encode() in bytes(data)
                found = found or (matches_path and security.SecTrustedApplicationValidateWithPath(application, str(BINARY).encode()) == 0)
            if action == "trust" and not found:
                status, trusted = security.SecTrustedApplicationCreateFromPath(str(BINARY).encode(), None)
                check(status)
                check(security.SecACLSetContents(acl, [*applications, trusted], description, selector))
                changed = True
        if changed:
            check(security.SecKeychainItemSetAccess(item, access))
        return dict(installed_app_trusted=found or changed, changed=changed, scope="existing-asr-item-only")
    finally:
        security.SecKeychainSetUserInteractionAllowed(True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "trust"))
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("仅适用于 macOS")
    with (APP / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleIdentifier") != "io.github.sjtu-learning-assistant":
        raise SystemExit("不是指定的学习助手 App")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
    import Security
    print(json.dumps(run(args.action, Security), ensure_ascii=False))


if __name__ == "__main__":
    main()
