"""Provider-neutral cloud directory and metadata service."""
from __future__ import annotations

import inspect
from typing import Any, Iterable, Protocol, runtime_checkable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.models import CloudFile
from sjtu_learning_assistant.repository import CloudFileUpsertResult, upsert_cloud_files


@runtime_checkable
class CloudProvider(Protocol):
    """Minimal contract implemented by Pan/cloud adapters outside this module."""

    def list_directory(self, path: Any = (), *, page: int = 1, page_size: int = 100) -> Any: ...

    def download_temp(self, path: Any) -> Any: ...


class CloudService:
    def __init__(
        self,
        engine: Engine,
        providers: dict[str, CloudProvider] | None = None,
    ) -> None:
        self.engine = engine
        self._providers: dict[str, CloudProvider] = dict(providers or {})

    def register_provider(self, name: str, provider: CloudProvider) -> None:
        if not name:
            raise ValueError("provider name 不能为空")
        self._providers[name] = provider

    def provider(self, name: str) -> CloudProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"未配置云盘 Provider：{name}") from exc

    def browse(
        self,
        provider: str,
        remote_id: str | None = None,
        *,
        account_id: str = "default",
    ) -> tuple[CloudFile, ...]:
        """Read a directory from a provider and atomically refresh local metadata."""
        adapter = self.provider(provider)
        path: Any = remote_id if remote_id is not None else ()
        page_number = 1
        items: list[Any] = []
        parameters = inspect.signature(adapter.list_directory).parameters.values()
        supports_paging = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            or parameter.name in {"page", "page_size"}
            for parameter in parameters
        )
        while True:
            if supports_paging:
                result = adapter.list_directory(path, page=page_number, page_size=100)
            else:
                result = adapter.list_directory(remote_id)
            page_items = getattr(result, "items", result)
            items.extend(list(page_items))
            if not supports_paging or not bool(getattr(result, "has_more", False)):
                break
            page_number += 1
        return self.upsert_metadata(
            provider, items, account_id=account_id, parent_remote_id=remote_id
        ).records

    list_directory = browse

    def upsert_metadata(
        self,
        provider: str,
        files: Iterable[Any],
        *,
        account_id: str = "default",
        parent_remote_id: str | None = None,
    ) -> CloudFileUpsertResult:
        return upsert_cloud_files(
            self.engine,
            provider=provider,
            account_id=account_id,
            parent_remote_id=parent_remote_id,
            files=files,
        )

    def get_file(self, cloud_file_id: int) -> CloudFile | None:
        with Session(self.engine) as session:
            record = session.scalar(select(CloudFile).where(CloudFile.id == cloud_file_id))
            if record is not None:
                session.expunge(record)
            return record

    def download_temp(self, provider: str, remote_id: str) -> Any:
        """Return the provider-owned temporary download/context unchanged."""
        return self.provider(provider).download_temp(remote_id)
