"""本机非敏感设置：严格校验并以原子 JSON 持久化。"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from sjtu_learning_assistant.archive_service import DEFAULT_ARCHIVE_ROOT
from sjtu_learning_assistant.database import APP_SUPPORT_DIR

SETTINGS_PATH = APP_SUPPORT_DIR / "settings.json"
ALLOWED_KEYS = frozenset(
    {
        "archive_root",
        "auto_download_current_term",
        "organize_by_category",
        "mail_account",
    }
)
LEGACY_KEYS = ALLOWED_KEYS - {"mail_account"}
SENSITIVE_KEY_PARTS = ("token", "password", "secret", "email", "credential")


class SettingsError(ValueError):
    """可安全展示的本机设置错误。"""


@dataclass(frozen=True)
class LocalSettings:
    archive_root: str = str(DEFAULT_ARCHIVE_ROOT)
    auto_download_current_term: bool = True
    organize_by_category: bool = True
    mail_account: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_archive_root(value: object) -> str:
    if not isinstance(value, str):
        raise SettingsError("归档目录必须是字符串。")
    if not value.strip() or any(ord(character) < 32 for character in value):
        raise SettingsError("归档目录不能为空且不能包含控制字符。")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise SettingsError("归档目录必须是绝对路径。")
    path = Path(os.path.abspath(path))
    if path == Path(path.anchor):
        raise SettingsError("不能将文件系统根目录设为归档目录。")

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            # tempfile returns /var/... on macOS although /var is a fixed system
            # alias for /private/var. Keep that one platform alias test-friendly;
            # user-controlled symlink components remain forbidden.
            system_aliases = dict(((Path("/var"), Path("/private/var")), (Path("/tmp"), Path("/private/tmp"))))
            if current in system_aliases and current.resolve() == system_aliases[current]:
                continue
            raise SettingsError("归档目录不能包含符号链接。")
        if not stat.S_ISDIR(mode):
            raise SettingsError("归档目录路径包含非目录对象。")
    return str(path)


def validate_mail_account(value: object) -> str:
    if not isinstance(value, str):
        raise SettingsError("邮箱账号必须是字符串。")
    account = value.strip()
    if len(account) > 320 or any(ord(character) < 32 for character in account):
        raise SettingsError("邮箱账号不能超过 320 个字符或包含控制字符。")
    return account


def _validate_mapping(payload: object, *, partial: bool) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SettingsError("设置参数必须是对象。")
    keys = set(payload)
    sensitive = [
        key
        for key in keys
        if isinstance(key, str)
        and any(part in key.casefold() for part in SENSITIVE_KEY_PARTS)
    ]
    if sensitive:
        raise SettingsError("本地设置禁止保存密码、令牌或其他凭据。")
    unknown = keys - ALLOWED_KEYS
    if unknown:
        raise SettingsError("设置包含不支持的字段。")
    if not partial and keys != ALLOWED_KEYS:
        raise SettingsError("本地设置文件字段不完整。")
    if partial and not keys:
        raise SettingsError("没有可更新的设置。")

    result: dict[str, Any] = {}
    if "archive_root" in payload:
        result["archive_root"] = validate_archive_root(payload["archive_root"])
    if "mail_account" in payload:
        result["mail_account"] = validate_mail_account(payload["mail_account"])
    for key in ("auto_download_current_term", "organize_by_category"):
        if key in payload:
            value = payload[key]
            if type(value) is not bool:
                raise SettingsError(f"{key} 必须是布尔值。")
            result[key] = value
    return result


def _parse_env_bool(name: str, value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"环境变量 {name} 必须是布尔值。")


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = Path(path).expanduser()

    def load(self, *, environ: Mapping[str, str] | None = None) -> LocalSettings:
        environment = os.environ if environ is None else environ
        initial_mail_account = validate_mail_account(environment.get("SJTU_EMAIL", ""))
        if not self.path.exists():
            return LocalSettings(mail_account=initial_mail_account)
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SettingsError("本地设置文件无法读取。") from exc
        if isinstance(payload, dict) and set(payload) == LEGACY_KEYS:
            payload = {**payload, "mail_account": initial_mail_account}
        values = _validate_mapping(payload, partial=False)
        return LocalSettings(**values)

    def save(self, settings: LocalSettings) -> LocalSettings:
        values = _validate_mapping(settings.to_dict(), partial=False)
        validated = LocalSettings(**values)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".settings-", suffix=".json", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            payload = (
                json.dumps(validated.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8")
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary_path.unlink(missing_ok=True)
        return validated

    def update(self, payload: object) -> LocalSettings:
        changes = _validate_mapping(payload, partial=True)
        values = self.load().to_dict()
        values.update(changes)
        return self.save(LocalSettings(**values))

    def resolve(
        self,
        *,
        archive_root: Path | str | None = None,
        auto_download_current_term: bool | None = None,
        organize_by_category: bool | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> LocalSettings:
        environment = os.environ if environ is None else environ
        values = self.load(environ=environment).to_dict()
        if "SJTU_ARCHIVE_ROOT" in environment:
            values["archive_root"] = validate_archive_root(environment["SJTU_ARCHIVE_ROOT"])
        if "SJTU_AUTO_DOWNLOAD_CURRENT_TERM" in environment:
            values["auto_download_current_term"] = _parse_env_bool(
                "SJTU_AUTO_DOWNLOAD_CURRENT_TERM",
                environment["SJTU_AUTO_DOWNLOAD_CURRENT_TERM"],
            )
        if "SJTU_ORGANIZE_BY_CATEGORY" in environment:
            values["organize_by_category"] = _parse_env_bool(
                "SJTU_ORGANIZE_BY_CATEGORY",
                environment["SJTU_ORGANIZE_BY_CATEGORY"],
            )
        if archive_root is not None:
            values["archive_root"] = validate_archive_root(str(archive_root))
        if auto_download_current_term is not None:
            if type(auto_download_current_term) is not bool:
                raise SettingsError("auto_download_current_term 必须是布尔值。")
            values["auto_download_current_term"] = auto_download_current_term
        if organize_by_category is not None:
            if type(organize_by_category) is not bool:
                raise SettingsError("organize_by_category 必须是布尔值。")
            values["organize_by_category"] = organize_by_category
        return LocalSettings(**_validate_mapping(values, partial=False))
