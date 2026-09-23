from __future__ import annotations

import importlib.util
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import Session

from sjtu_learning_assistant.cloud_service import CloudService
from sjtu_learning_assistant.models import Base, CloudFile


class FakeProvider:
    def list_directory(self, remote_id=None):
        return [
            {
                "id": "file-1",
                "parent_id": remote_id,
                "name": "first.txt",
                "size": 5,
                "mime_type": "text/plain",
            }
        ]

    def download_temp(self, _remote_id):
        raise NotImplementedError


@dataclass(frozen=True)
class CloudItemLike:
    name: str
    path: tuple[str, ...]
    is_directory: bool
    size: int | None
    content_type: str | None
    etag: str | None
    created_at: str | None
    modified_at: str | None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PageLike:
    items: tuple[CloudItemLike, ...]
    page: int
    page_size: int
    total: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


class CloudServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.service = CloudService(self.engine, {"fake": FakeProvider()})

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_browse_upserts_metadata(self) -> None:
        first = self.service.browse("fake", "folder-1")
        second = self.service.browse("fake", "folder-1")
        self.assertEqual("file-1", first[0].remote_id)
        self.assertEqual(first[0].id, second[0].id)
        with Session(self.engine) as session:
            self.assertEqual(1, session.scalar(select(func.count(CloudFile.id))))
            record = session.scalar(select(CloudFile))
            self.assertEqual("folder-1", record.parent_remote_id)
            self.assertEqual("text/plain", record.content_type)

    def test_cloud_item_mapping_and_pagination(self) -> None:
        class Provider:
            def list_directory(self, _path=(), *, page=1, page_size=100):
                item = CloudItemLike(
                    name=f"item-{page}.pdf",
                    path=("folder", f"item-{page}.pdf"),
                    is_directory=False,
                    size=page * 10,
                    content_type="application/pdf",
                    etag=f"etag-{page}",
                    created_at="2026-09-20T00:00:00Z",
                    modified_at="2026-09-21T00:00:00Z",
                    metadata={
                        "id": f"remote-{page}",
                        "parentItemId": "remote-folder",
                        "download_url": f"https://download.example/{page}",
                    },
                )
                return PageLike((item,), page, page_size, 101)

            def download_temp(self, _path):
                raise NotImplementedError

        service = CloudService(self.engine, {"page": Provider()})
        records = service.browse("page", "folder")
        self.assertEqual(["remote-1", "remote-2"], [item.remote_id for item in records])
        self.assertEqual("remote-folder", records[0].parent_remote_id)
        self.assertEqual("folder/item-1.pdf", records[0].path)
        self.assertEqual("application/pdf", records[0].content_type)
        self.assertEqual("https://download.example/1", records[0].download_url)
        self.assertEqual("etag-1", records[0].raw_data["etag"])

    def test_orm_and_migration_schema_contract_match(self) -> None:
        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "0011_add_cloud_files_and_submissions.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0011", migration_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        class CaptureOperations:
            def __init__(self) -> None:
                self.tables = {}
                self.indexes = {}
                self.dropped = []

            def create_table(self, name, *elements):
                self.tables[name] = elements

            def create_index(self, name, table_name, columns, **_kwargs):
                self.indexes[name] = (table_name, tuple(columns))

            def drop_index(self, name, table_name=None):
                self.dropped.append(("index", name, table_name))

            def drop_table(self, name):
                self.dropped.append(("table", name))

        capture = CaptureOperations()
        module.op = capture
        module.upgrade()
        for table_name in ("cloud_files", "submissions"):
            orm_table = Base.metadata.tables[table_name]
            migration_elements = capture.tables[table_name]
            migration_columns = {
                element.name: element
                for element in migration_elements
                if hasattr(element, "type") and hasattr(element, "nullable")
            }
            self.assertEqual(set(orm_table.columns.keys()), set(migration_columns))
            for column in orm_table.columns:
                migrated = migration_columns[column.name]
                self.assertEqual(column.nullable, migrated.nullable)
                self.assertEqual(column.type._type_affinity, migrated.type._type_affinity)
                self.assertEqual(
                    getattr(column.type, "length", None),
                    getattr(migrated.type, "length", None),
                )
                def default_text(default):
                    if default is None:
                        return None
                    return str(getattr(default, "arg", default))

                self.assertEqual(
                    default_text(column.server_default),
                    default_text(migrated.server_default),
                )
            orm_constraint_names = {
                constraint.name
                for constraint in orm_table.constraints
                if isinstance(constraint, (CheckConstraint, ForeignKeyConstraint, UniqueConstraint))
                and constraint.name
            }
            migration_constraint_names = {
                constraint.name
                for constraint in migration_elements
                if isinstance(constraint, (CheckConstraint, ForeignKeyConstraint, UniqueConstraint))
                and constraint.name
            }
            self.assertEqual(orm_constraint_names, migration_constraint_names)
        self.assertEqual(
            {
                index.name: (index.table.name, tuple(column.name for column in index.columns))
                for table in (CloudFile.__table__, Base.metadata.tables["submissions"])
                for index in table.indexes
            },
            capture.indexes,
        )
        module.downgrade()
        self.assertEqual(
            [
                ("index", "ix_submissions_canvas_assignment", "submissions"),
                ("table", "submissions"),
                ("index", "ix_cloud_files_parent", "cloud_files"),
                ("table", "cloud_files"),
            ],
            capture.dropped,
        )



if __name__ == "__main__":
    unittest.main()
