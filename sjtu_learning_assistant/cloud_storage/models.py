"""Stable, provider-neutral cloud storage value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True)
class SpaceCredential:
    access_token: str = field(repr=False)
    library_id: str
    space_id: str
    expires_at: datetime

    def is_expired(self, *, leeway_seconds: int = 60) -> bool:
        now = datetime.now(timezone.utc)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return (expires - now).total_seconds() <= leeway_seconds


@dataclass(frozen=True)
class SpaceInfo:
    capacity: int
    used: int
    available: int
    has_personal_space: bool = True


@dataclass(frozen=True)
class CloudItem:
    name: str
    path: tuple[str, ...]
    is_directory: bool
    size: int | None = None
    content_type: str | None = None
    etag: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class CloudDirectoryPage:
    items: tuple[CloudItem, ...]
    page: int
    page_size: int
    total: int
    path: tuple[str, ...] = ()

    @property
    def has_more(self) -> bool:
        if self.total <= 0:
            return self.page_size > 0 and len(self.items) >= self.page_size
        # A short page whose position exactly reaches total is the honoured last page.
        if (
            len(self.items) < self.page_size
            and (self.page - 1) * self.page_size + len(self.items) == self.total
        ):
            return False
        return bool(self.items) and self.page * len(self.items) < self.total


@dataclass
class UploadSession:
    """Serializable multipart state without tokens, signed headers, or trusted COS targets."""

    remote_path: tuple[str, ...]
    confirm_key: str
    overwrite: bool = False
    next_part: int = 1
    uploaded_parts: list[int] = field(default_factory=list)
    part_sizes: dict[int, int] = field(default_factory=dict)
    # Runtime-only values. A resume must replace all of them using a renew response.
    domain: str = field(default="", repr=False)
    object_path: str = field(default="", repr=False)
    upload_id: str = field(default="", repr=False)
    expiration: str | None = field(default=None, repr=False)
    authority_verified: bool = field(default=False, repr=False, init=False)

    @property
    def bytes_uploaded(self) -> int:
        return sum(self.part_sizes.get(part, 0) for part in self.uploaded_parts)

    def validate_contiguous(self) -> None:
        if type(self.next_part) is not int or self.next_part < 1:
            raise ValueError("上传会话的下一个分片序号无效。")
        # 先比较长度，避免篡改的超大 next_part 让 range() 物化出巨大列表。
        if len(self.uploaded_parts) != self.next_part - 1:
            raise ValueError("上传会话的已完成分片不是连续前缀。")
        expected = list(range(1, self.next_part))
        if self.uploaded_parts != expected or set(self.part_sizes) != set(expected):
            raise ValueError("上传会话的已完成分片不是连续前缀。")
        if any(type(size) is not int or size <= 0 for size in self.part_sizes.values()):
            raise ValueError("上传会话包含无效分片大小。")

    def to_dict(self) -> dict[str, Any]:
        self.validate_contiguous()
        return {
            "remote_path": list(self.remote_path),
            "confirm_key": self.confirm_key,
            "overwrite": self.overwrite,
            "next_part": self.next_part,
            "uploaded_parts": list(self.uploaded_parts),
            "part_sizes": {str(part): size for part, size in self.part_sizes.items()},
            "bytes_uploaded": self.bytes_uploaded,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UploadSession":
        # Legacy runtime target fields are accepted only to discard them. They are never trusted.
        allowed = {
            "remote_path", "confirm_key", "overwrite", "next_part", "uploaded_parts",
            "part_sizes", "bytes_uploaded", "domain", "object_path", "upload_id", "expiration",
        }
        if not isinstance(value, Mapping) or set(value) - allowed:
            raise ValueError("上传会话包含未知或不安全字段。")
        part_sizes_value = value.get("part_sizes", {})
        if not isinstance(part_sizes_value, Mapping):
            raise ValueError("上传会话分片大小格式错误。")
        remote_path_value = value.get("remote_path")
        confirm_key_value = value.get("confirm_key")
        if (
            not isinstance(remote_path_value, (list, tuple))
            or not remote_path_value
            or not isinstance(confirm_key_value, str)
            or not confirm_key_value
        ):
            raise ValueError("上传会话格式错误。")
        # 持久化会话可能被篡改或损坏：严格校验类型，不做 bool("false")、int(2.9)、int(True) 之类的隐式转换。
        overwrite_value = value.get("overwrite", False)
        uploaded_value = value.get("uploaded_parts", [])
        if (
            not isinstance(overwrite_value, bool)
            or not isinstance(uploaded_value, (list, tuple))
            or not confirm_key_value.strip()
            or any(
                not isinstance(part, str) or not part.strip()
                or part in {".", ".."} or "/" in part or "\\" in part or "\x00" in part
                for part in remote_path_value
            )
        ):
            raise ValueError("上传会话格式错误。")

        def strict_int(raw: Any, *, allow_digit_string: bool = False) -> int:
            if type(raw) is int:
                return raw
            if allow_digit_string and isinstance(raw, str) and raw.isascii() and raw.isdigit():
                return int(raw)
            raise ValueError("上传会话格式错误。")

        # JSON 对象键总是字符串，所以分片序号允许十进制数字串；分片大小必须是正整数（空分片不会被上传）。
        part_sizes = {}
        for part, size in part_sizes_value.items():
            index = strict_int(part, allow_digit_string=True)
            if index in part_sizes:
                raise ValueError("上传会话包含重复分片序号。")
            part_sizes[index] = strict_int(size)
        if any(size <= 0 for size in part_sizes.values()):
            raise ValueError("上传会话包含无效分片大小。")
        session = cls(
            remote_path=tuple(remote_path_value),
            confirm_key=confirm_key_value,
            overwrite=overwrite_value,
            next_part=strict_int(value.get("next_part", 1)),
            uploaded_parts=[strict_int(part) for part in uploaded_value],
            part_sizes=part_sizes,
        )
        session.validate_contiguous()
        claimed_bytes = strict_int(value.get("bytes_uploaded", session.bytes_uploaded))
        if claimed_bytes != session.bytes_uploaded:
            raise ValueError("上传会话的连续前缀大小不一致。")
        return session
