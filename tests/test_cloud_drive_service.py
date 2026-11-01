from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from desktop_app import DesktopBridge, _authorize_native_drop_paths
from sjtu_learning_assistant.cloud_drive_service import (
    CloudDriveError,
    CloudDriveService,
    TransferCancelled,
    cloud_error_code,
    iter_folder_files,
    numbered_name,
    safe_local_filename,
    sort_items,
    unique_local_path,
    validate_item_name,
)
from sjtu_learning_assistant.cloud_storage import (
    CloudAuthError,
    CloudConflictError,
    CloudItem,
    CloudNetworkError,
    CloudNotFoundError,
    CloudTimeoutError,
    SpaceInfo,
)


def item(path, *, directory=False, size=None, modified=None):
    return CloudItem(
        name=path[-1], path=tuple(path), is_directory=directory,
        size=size, modified_at=modified,
    )


class FakePan:
    """In-memory 交大云盘 with the provider surface used by CloudDriveService."""

    def __init__(self):
        self.nodes: dict[tuple[str, ...], CloudItem] = {}
        self.blobs: dict[tuple[str, ...], bytes] = {}
        self.calls: list[tuple] = []
        self.list_calls = 0
        self.fail_download_after: int | None = None

    def add(self, *path, directory=False, data=b"", modified="2026-10-01T08:00:00Z"):
        self.nodes[tuple(path)] = item(
            path, directory=directory, size=None if directory else len(data), modified=modified,
        )
        if not directory:
            self.blobs[tuple(path)] = data

    def get_space_info(self):
        return SpaceInfo(capacity=1024, used=233, available=791)

    def iter_directory(self, path, *, page_size=100):
        self.list_calls += 1
        parent = tuple(path)
        if parent and (parent not in self.nodes or not self.nodes[parent].is_directory):
            raise CloudNotFoundError("列举目录失败：项目不存在。", status_code=404)
        for key, value in list(self.nodes.items()):
            if key[:-1] == parent:
                yield value

    def get_info(self, path):
        key = tuple(path)
        if key not in self.nodes:
            raise CloudNotFoundError("读取项目信息失败：项目不存在。", status_code=404)
        return self.nodes[key]

    def exists(self, path):
        return tuple(path) in self.nodes

    def create_directory(self, path):
        key = tuple(path)
        self.calls.append(("mkdir", key))
        if key in self.nodes:
            raise CloudConflictError("SameNameDirectoryOrFileExists", status_code=409)
        self.add(*key, directory=True)

    def ensure_directory(self, path):
        current = ()
        for part in path:
            current += (part,)
            if current not in self.nodes:
                self.add(*current, directory=True)

    def _relocate(self, source, destination, *, keep):
        if destination in self.nodes:
            raise CloudConflictError("目标已存在", status_code=409)
        for key in sorted(k for k in self.nodes if k[: len(source)] == source):
            new_key = destination + key[len(source):]
            node = self.nodes[key]
            self.nodes[new_key] = item(new_key, directory=node.is_directory, size=node.size, modified=node.modified_at)
            if key in self.blobs:
                self.blobs[new_key] = self.blobs[key]
            if not keep:
                self.nodes.pop(key)
                self.blobs.pop(key, None)
        return self.nodes[destination]

    def move(self, source, destination, *, overwrite=False, directory=False):
        self.calls.append(("move", tuple(source), tuple(destination), directory))
        return self._relocate(tuple(source), tuple(destination), keep=False)

    def copy(self, source, destination, *, overwrite=False, directory=False):
        self.calls.append(("copy", tuple(source), tuple(destination), directory))
        return self._relocate(tuple(source), tuple(destination), keep=True)

    def delete(self, path, *, permanent=False, missing_ok=True, directory=False):
        self.calls.append(("delete", tuple(path), permanent, directory))
        for key in [k for k in self.nodes if k[: len(path)] == tuple(path)]:
            self.nodes.pop(key)

    def _upload(self, path, source, overwrite):
        key = tuple(path)
        if key in self.nodes and not overwrite:
            raise CloudConflictError("SameNameDirectoryOrFileExists", status_code=409)
        chunks = []
        while True:
            chunk = source.read(3)
            if not chunk:
                break
            chunks.append(chunk)
        self.add(*key, data=b"".join(chunks))
        return self.nodes[key]

    def simple_upload(self, path, source, *, overwrite=False):
        self.calls.append(("simple_upload", tuple(path), overwrite))
        return self._upload(path, source, overwrite)

    def multipart_upload(self, path, source, *, overwrite=False, chunk_size=4):
        self.calls.append(("multipart_upload", tuple(path), overwrite))
        return self._upload(path, source, overwrite)

    def download_stream(self, path, *, chunk_size=65536):
        data = self.blobs[tuple(path)]
        for index in range(0, len(data), 2):
            if self.fail_download_after is not None and index >= self.fail_download_after:
                raise CloudTimeoutError("交大云盘下载超时。")
            yield data[index:index + 2]


class HelperTests(unittest.TestCase):
    def test_validate_item_name(self):
        self.assertEqual("新建文件夹", validate_item_name("  新建文件夹 "))
        for bad in ("", "  ", ".", "..", "a/b", "a\\b", "a\x00b", "x" * 256, None, 3):
            with self.assertRaises(CloudDriveError):
                validate_item_name(bad)

    def test_safe_local_filename_handles_windows_rules(self):
        self.assertEqual("a_b_c.txt", safe_local_filename("a:b?c.txt"))
        self.assertEqual("_CON.txt", safe_local_filename("CON.txt"))
        self.assertEqual("_nul", safe_local_filename("nul"))
        self.assertEqual("下载文件", safe_local_filename("..."))
        self.assertEqual("report", safe_local_filename("report. "))
        self.assertLessEqual(len(safe_local_filename("x" * 300 + ".pdf")), 200)
        self.assertTrue(safe_local_filename("x" * 300 + ".pdf").endswith(".pdf"))

    def test_numbered_name(self):
        self.assertEqual("report (2).pdf", numbered_name("report.pdf", 2))
        self.assertEqual("资料 (1)", numbered_name("资料", 1))
        self.assertEqual(".env (1)", numbered_name(".env", 1))

    def test_sort_puts_folders_first_with_natural_order(self):
        values = [
            item(("第10周.pdf",), size=5, modified="2026-10-03T00:00:00Z"),
            item(("第2周.pdf",), size=50, modified="2026-10-01T00:00:00Z"),
            item(("b",), directory=True),
            item(("A",), directory=True),
        ]
        self.assertEqual(["A", "b", "第2周.pdf", "第10周.pdf"], [v.name for v in sort_items(values, by="name", order="asc")])
        self.assertEqual(["b", "A", "第10周.pdf", "第2周.pdf"], [v.name for v in sort_items(values, by="name", order="desc")])
        self.assertEqual(["A", "b", "第2周.pdf", "第10周.pdf"], [v.name for v in sort_items(values, by="size", order="desc")])
        self.assertEqual(["第10周.pdf", "第2周.pdf"], [v.name for v in sort_items(values, by="modified", order="desc")][2:])

    def test_error_codes(self):
        self.assertEqual("auth_required", cloud_error_code(CloudAuthError("x")))
        self.assertEqual("not_found", cloud_error_code(CloudNotFoundError("x")))
        self.assertEqual("conflict", cloud_error_code(CloudConflictError("x")))
        self.assertEqual("network_error", cloud_error_code(CloudTimeoutError("x")))
        self.assertEqual("network_error", cloud_error_code(CloudNetworkError("x")))
        self.assertEqual("cancelled", cloud_error_code(TransferCancelled("x")))
        self.assertEqual("operation_failed", cloud_error_code(CloudDriveError("x")))

    def test_unique_local_path_and_folder_walk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("1")
            self.assertEqual("a (1).txt", unique_local_path(root, "a.txt").name)
            folder = root / "课件"
            (folder / "第1周").mkdir(parents=True)
            (folder / "第1周" / "slides.pdf").write_text("x")
            (folder / "readme.md").write_text("x")
            (folder / ".DS_Store").write_text("x")
            (folder / "Thumbs.db").write_text("x")
            found = sorted((p.name, rel) for p, rel in iter_folder_files(folder))
            self.assertEqual([("readme.md", ("课件",)), ("slides.pdf", ("课件", "第1周"))], found)
            self.assertEqual(2, len(iter_folder_files(folder, max_files=2)))
            with self.assertRaises(CloudDriveError):
                iter_folder_files(folder, max_files=1)
            with self.assertRaises(CloudDriveError):
                iter_folder_files(folder, max_depth=1)


class CloudDriveServiceTests(unittest.TestCase):
    def setUp(self):
        self.pan = FakePan()
        self.pan.add("课程", directory=True)
        self.pan.add("课程", "第1周.pdf", data=b"hello world")
        self.pan.add("作业.docx", data=b"abc", modified="2026-10-20T00:00:00Z")
        self.pan.add("Notes.txt", data=b"abcdef", modified="2026-10-10T00:00:00Z")
        self.now = [0.0]
        self.drive = CloudDriveService(lambda: self.pan, clock=lambda: self.now[0])

    def test_space(self):
        self.assertEqual({"capacity": 1024, "used": 233, "available": 791, "has_personal_space": True}, self.drive.space())

    def test_list_paginates_sorts_searches_and_caches(self):
        page = self.drive.list((), page=1, page_size=2)
        self.assertEqual(["课程", "Notes.txt"], [entry["name"] for entry in page["items"]])
        self.assertEqual((3, 2, 1, 2), (page["total"], page["pages"], page["folder_count"], page["file_count"]))
        self.assertIsNone(page["items"][0]["size"])
        second = self.drive.list((), page=9, page_size=2)
        self.assertEqual(2, second["page"])
        self.assertEqual(["作业.docx"], [entry["name"] for entry in second["items"]])
        newest = self.drive.list((), sort_by="modified", order="desc")
        self.assertEqual(["课程", "作业.docx", "Notes.txt"], [entry["name"] for entry in newest["items"]])
        found = self.drive.list((), query="NOTES")
        self.assertEqual(["Notes.txt"], [entry["name"] for entry in found["items"]])
        self.assertEqual(1, self.pan.list_calls)
        self.drive.list((), refresh=True)
        self.assertEqual(2, self.pan.list_calls)
        self.now[0] = 100
        self.drive.list(())
        self.assertEqual(3, self.pan.list_calls)
        with self.assertRaises(CloudDriveError):
            self.drive.list((), sort_by="evil")

    def test_list_missing_folder_is_not_found(self):
        with self.assertRaises(CloudNotFoundError):
            self.drive.list(("不存在",))

    def test_concurrent_invalidation_does_not_cache_old_listing(self):
        fetched = threading.Event()
        resume = threading.Event()
        original = self.pan.iter_directory

        def delayed(path, *, page_size):
            snapshot = list(original(path, page_size=page_size))
            fetched.set()
            self.assertTrue(resume.wait(5))
            yield from snapshot

        self.pan.iter_directory = delayed
        worker = threading.Thread(target=lambda: self.drive.list(()))
        worker.start()
        try:
            self.assertTrue(fetched.wait(5))
            self.drive.create_folder((), "新目录")
        finally:
            resume.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.pan.iter_directory = original
        self.assertIn("新目录", [item["name"] for item in self.drive.list(())["items"]])

    def test_create_folder_and_conflict(self):
        created = self.drive.create_folder((), "实验报告")
        self.assertTrue(created["is_directory"])
        self.assertIn("实验报告", [entry["name"] for entry in self.drive.list(())["items"]])
        with self.assertRaises(CloudConflictError):
            self.drive.create_folder((), "实验报告")
        with self.assertRaises(CloudDriveError):
            self.drive.create_folder((), "../x")

    def test_rename_passes_directory_flag_and_invalidates_cache(self):
        self.drive.list(())
        renamed = self.drive.rename(("课程",), "课程资料", is_directory=True)
        self.assertEqual(["课程资料"], renamed["path"])
        self.assertIn(("move", ("课程",), ("课程资料",), True), self.pan.calls)
        self.assertIn("课程资料", [entry["name"] for entry in self.drive.list(())["items"]])
        with self.assertRaises(CloudConflictError):
            self.drive.rename(("Notes.txt",), "作业.docx")
        with self.assertRaises(CloudDriveError):
            self.drive.rename((), "x")

    def test_move_copy_and_guards(self):
        result = self.drive.transfer([(("Notes.txt",), False)], ("课程",))
        self.assertEqual([["Notes.txt"]], result["done"])
        self.assertIn(("课程", "Notes.txt"), self.pan.nodes)
        copied = self.drive.transfer([(("课程", "Notes.txt"), False)], ("课程",), copy=True)
        self.assertEqual([], copied["failed"])
        self.assertIn(("课程", "Notes (1).txt"), self.pan.nodes)
        into_self = self.drive.transfer([(("课程",), True)], ("课程",))
        self.assertEqual("operation_failed", into_self["failed"][0]["code"])
        self.pan.add("课程", "作业.docx", data=b"x")
        conflict = self.drive.transfer([(("作业.docx",), False)], ("课程",))
        self.assertEqual("conflict", conflict["failed"][0]["code"])
        with self.assertRaises(CloudNotFoundError):
            self.drive.transfer([(("作业.docx",), False)], ("没有",))
        with self.assertRaises(CloudDriveError):
            self.drive.transfer([(("作业.docx",), False)], ("课程", "第1周.pdf"))

    def test_delete_goes_to_recycle_bin(self):
        result = self.drive.delete([(("Notes.txt",), False), (("课程",), True)])
        self.assertEqual(2, len(result["done"]))
        self.assertIn(("delete", ("Notes.txt",), False, False), self.pan.calls)
        self.assertIn(("delete", ("课程",), False, True), self.pan.calls)
        with self.assertRaises(CloudDriveError):
            self.drive.delete([])

    def test_upload_simple_multipart_conflict_and_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "作业.docx"
            local.write_bytes(b"new content")
            with self.assertRaises(CloudConflictError):
                self.drive.upload(str(local), ())
            renamed = self.drive.upload(str(local), (), conflict="rename", transfer_id="upload-0001")
            self.assertEqual(["作业 (1).docx"], renamed["item"]["path"])
            overwritten = self.drive.upload(str(local), (), conflict="overwrite")
            self.assertEqual(11, overwritten["item"]["size"])
            nested = self.drive.upload(str(local), ("新目录", "子目录"), ensure_parent=True)
            self.assertEqual(["新目录", "子目录", "作业.docx"], nested["item"]["path"])
            transfers = {entry["id"]: entry for entry in self.drive.transfers.snapshot()}
            self.assertEqual("done", transfers["upload-0001"]["status"])
            self.assertEqual(11, transfers["upload-0001"]["done"])
            from sjtu_learning_assistant import cloud_drive_service as module

            original = module.SIMPLE_UPLOAD_LIMIT
            module.SIMPLE_UPLOAD_LIMIT = 4
            try:
                self.drive.upload(str(local), ("课程",))
            finally:
                module.SIMPLE_UPLOAD_LIMIT = original
            self.assertEqual("multipart_upload", self.pan.calls[-1][0])
            with self.assertRaises(CloudDriveError):
                self.drive.upload(str(Path(directory) / "missing.txt"), ())
            with self.assertRaises(CloudDriveError):
                self.drive.upload(str(local), (), conflict="nuke")

    def test_upload_size_boundary_without_allocating_file_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "large.bin"
            local.write_bytes(b"x")
            self.pan.multipart_upload = Mock(return_value=item(("large.bin",), size=2 * 1024**3))
            with patch("sjtu_learning_assistant.cloud_drive_service.os.fstat") as fstat:
                fstat.return_value.st_size = 2 * 1024**3
                self.drive.upload(str(local), ())
                fstat.return_value.st_size += 1
                with self.assertRaisesRegex(CloudDriveError, "2 GiB"):
                    self.drive.upload(str(local), ())
            self.pan.multipart_upload.assert_called_once()

    def test_upload_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "big.bin"
            local.write_bytes(b"x" * 30)
            original = self.drive.transfers.advance

            def cancel_then_advance(transfer, amount):
                self.drive.cancel(transfer.transfer_id)
                original(transfer, amount)

            self.drive.transfers.advance = cancel_then_advance
            with self.assertRaises(TransferCancelled):
                self.drive.upload(str(local), (), transfer_id="cancel-0001")
            snapshot = {entry["id"]: entry for entry in self.drive.transfers.snapshot()}
            self.assertEqual("cancelled", snapshot["cancel-0001"]["status"])

    def test_upload_rejects_source_truncated_after_open(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "file.bin"
            local.write_bytes(b"original bytes")
            original = self.pan.simple_upload

            def truncated(path, source, *, overwrite=False):
                local.write_bytes(b"short")
                return original(path, source, overwrite=overwrite)

            self.pan.simple_upload = truncated
            with self.assertRaisesRegex(CloudDriveError, "变短"):
                self.drive.upload(str(local), (), transfer_id="short-file")
            self.assertNotIn(("file.bin",), self.pan.nodes)
            self.assertEqual("failed", self.drive.transfers.snapshot()[-1]["status"])

    def test_simple_upload_rejects_truncated_read_all(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "file.bin"
            local.write_bytes(b"original bytes")

            def truncated(path, source, *, overwrite=False):
                local.write_bytes(b"short")
                source.read()
                self.fail("a shortened source must not be committed")

            self.pan.simple_upload = truncated
            with self.assertRaisesRegex(CloudDriveError, "变短"):
                self.drive.upload(str(local), ())

    def test_multipart_cancel_after_last_chunk_before_confirm(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "file.bin"
            local.write_bytes(b"last chunk")

            def multipart(path, source, *, overwrite=False, chunk_size):
                self.assertEqual(b"last chunk", source.read(chunk_size))
                self.assertTrue(self.drive.cancel("last-chunk")["cancelled"])
                source.read(chunk_size)
                self.fail("cancelled upload must not be confirmed")

            self.pan.multipart_upload = multipart
            with patch("sjtu_learning_assistant.cloud_drive_service.SIMPLE_UPLOAD_LIMIT", 1):
                with self.assertRaises(TransferCancelled):
                    self.drive.upload(str(local), (), transfer_id="last-chunk")
            self.assertEqual("cancelled", self.drive.transfers.snapshot()[-1]["status"])

    def test_unexpected_upload_error_finishes_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "file.bin"
            local.write_bytes(b"x")
            self.pan.simple_upload = Mock(side_effect=RuntimeError("unexpected"))
            with self.assertRaises(RuntimeError):
                self.drive.upload(str(local), (), transfer_id="upload-error")
            self.assertEqual("failed", self.drive.transfers.snapshot()[-1]["status"])

    def test_download_atomic_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "第1周.pdf"
            result = self.drive.download(("课程", "第1周.pdf"), str(target), transfer_id="download-01")
            self.assertEqual({"transfer_id": "download-01", "name": "第1周.pdf", "size": 11, "status": "downloaded"}, result)
            self.assertEqual(b"hello world", target.read_bytes())
            self.pan.fail_download_after = 4
            broken = Path(directory) / "broken.pdf"
            with self.assertRaises(CloudTimeoutError):
                self.drive.download(("课程", "第1周.pdf"), str(broken))
            self.assertEqual(["第1周.pdf"], sorted(path.name for path in Path(directory).iterdir()))
            with self.assertRaises(CloudDriveError):
                self.drive.download(("课程",), str(Path(directory) / "x"))
            with self.assertRaises(CloudDriveError):
                self.drive.download(("课程", "第1周.pdf"), str(Path(directory) / "missing" / "x"))

    def test_download_tempfile_failure_finishes_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "第1周.pdf"
            with patch(
                "sjtu_learning_assistant.cloud_drive_service.tempfile.mkstemp",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(CloudDriveError):
                    self.drive.download(
                        ("课程", "第1周.pdf"),
                        str(target),
                        transfer_id="download-fail",
                    )
            snapshot = {item["id"]: item for item in self.drive.transfers.snapshot()}
            self.assertEqual("failed", snapshot["download-fail"]["status"])

    def test_cancel_before_download_commit_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.pdf"
            target.write_bytes(b"existing")
            with patch("sjtu_learning_assistant.cloud_drive_service.os.fsync") as fsync:
                fsync.side_effect = lambda _: self.assertTrue(self.drive.cancel("download-cancel")["cancelled"])
                with self.assertRaises(TransferCancelled):
                    self.drive.download(("课程", "第1周.pdf"), str(target), transfer_id="download-cancel")
            self.assertEqual(b"existing", target.read_bytes())
            self.assertEqual(["target.pdf"], [p.name for p in Path(directory).iterdir()])
            self.assertEqual("cancelled", self.drive.transfers.snapshot()[-1]["status"])

    def test_download_does_not_overwrite_file_created_during_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.pdf"
            with patch("sjtu_learning_assistant.cloud_drive_service.os.fsync") as fsync:
                fsync.side_effect = lambda _: target.write_bytes(b"other process")
                with self.assertRaisesRegex(CloudDriveError, "同名文件"):
                    self.drive.download(("课程", "第1周.pdf"), str(target), transfer_id="download-race")
            self.assertEqual(b"other process", target.read_bytes())
            self.assertEqual(["target.pdf"], [p.name for p in Path(directory).iterdir()])

    def test_duplicate_running_transfer_is_rejected(self):
        transfer = self.drive.transfers.start("same-id-01", kind="upload", name="a", total=1)
        with self.assertRaises(CloudDriveError):
            self.drive.transfers.start("same-id-01", kind="upload", name="a", total=1)
        self.drive.transfers.finish(transfer, "done")
        self.drive.transfers.start("same-id-01", kind="upload", name="a", total=1)
        with self.assertRaises(CloudDriveError):
            self.drive.transfers.start("bad id", kind="upload", name="a", total=1)


class FakeLearning:
    def __init__(self, pan):
        self.pan = pan

    def pan_provider(self):
        return self.pan


class DriveBridgeTests(unittest.TestCase):
    def setUp(self):
        self.pan = FakePan()
        self.pan.add("课程", directory=True)
        self.pan.add("课程", "第1周.pdf", data=b"hello")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.local = self.root / "上传.txt"
        self.local.write_bytes(b"payload")
        self.runner = Mock(return_value=Mock(returncode=0))
        self.saved = self.root / "下载.pdf"
        self.bridge = DesktopBridge(
            Mock(),
            FakeLearning(self.pan),
            multi_file_picker=lambda: [str(self.local)],
            folder_picker=lambda: str(self.root),
            save_file_picker=lambda name: str(self.saved),
            command_runner=self.runner,
        )

    def call(self, action, payload=None):
        return self.bridge.invoke(action, payload)

    def test_listing_and_validation(self):
        listed = self.call("drive_list", {"path": [], "page": 1, "page_size": 20, "sort_by": "name", "order": "asc"})
        self.assertTrue(listed["ok"])
        self.assertEqual(["课程"], [entry["name"] for entry in listed["data"]["items"]])
        self.assertEqual(791, self.call("drive_space")["data"]["available"])
        for payload in (
            {"path": "课程"},
            {"path": [".."]},
            {"path": ["a/b"]},
            {"path": [], "page": 0},
            {"path": [], "page_size": 500},
            {"path": [], "sort_by": "evil"},
            {"path": [], "refresh": "yes"},
            {"path": [], "query": 3},
            {"path": [], "extra": 1},
        ):
            self.assertEqual("operation_failed", self.call("drive_list", payload)["error"]["code"], payload)
        self.assertEqual("not_found", self.call("drive_list", {"path": ["没有"]})["error"]["code"])

    def test_auth_and_network_codes_are_distinct_for_drive_only(self):
        self.pan.get_space_info = Mock(side_effect=CloudAuthError("尚未设置交大云盘 UserToken。"))
        response = self.call("drive_space")
        self.assertEqual("auth_required", response["error"]["code"])
        self.assertEqual("尚未设置交大云盘 UserToken。", response["error"]["message"])
        self.pan.get_space_info = Mock(side_effect=CloudNetworkError("无法连接交大云盘服务。"))
        self.assertEqual("network_error", self.call("drive_space")["error"]["code"])

    def test_mkdir_rename_move_delete(self):
        self.assertTrue(self.call("drive_mkdir", {"parent": [], "name": "新建文件夹"})["ok"])
        self.assertEqual("conflict", self.call("drive_mkdir", {"parent": [], "name": "新建文件夹"})["error"]["code"])
        self.assertEqual("operation_failed", self.call("drive_mkdir", {"parent": [], "name": "a/b"})["error"]["code"])
        renamed = self.call("drive_rename", {"path": ["课程", "第1周.pdf"], "name": "week1.pdf"})
        self.assertEqual(["课程", "week1.pdf"], renamed["data"]["path"])
        self.assertEqual("operation_failed", self.call("drive_rename", {"path": [], "name": "x"})["error"]["code"])
        moved = self.call("drive_move", {"items": [{"path": ["课程", "week1.pdf"], "is_directory": False}], "destination": ["新建文件夹"]})
        self.assertEqual([["课程", "week1.pdf"]], moved["data"]["done"])
        for bad in ([], [{"path": []}], [{"path": ["a"], "is_directory": "no"}], [{"path": ["a"], "x": 1}], "a"):
            self.assertEqual("operation_failed", self.call("drive_delete", {"items": bad})["error"]["code"], bad)
        deleted = self.call("drive_delete", {"items": [{"path": ["新建文件夹"], "is_directory": True}]})
        self.assertEqual(1, len(deleted["data"]["done"]))

    def test_upload_requires_picker_or_drop_capability(self):
        forged = self.call("drive_upload", {"path": str(self.local), "parent": []})
        self.assertEqual("operation_failed", forged["error"]["code"])
        picked = self.call("drive_upload_pick")["data"]
        self.assertEqual(["上传.txt"], [entry["name"] for entry in picked["files"]])
        self.assertNotIn(str(self.root), picked["files"][0]["path"])
        second = self.call("drive_upload_pick")["data"]["files"][0]
        self.assertNotEqual(picked["files"][0]["path"], second["path"])
        uploaded = self.call("drive_upload", {"path": picked["files"][0]["path"], "parent": ["课程"], "transfer_id": "upload-abc1"})
        self.assertEqual(["课程", "上传.txt"], uploaded["data"]["item"]["path"])
        second_upload = self.call("drive_upload", {"path": second["path"], "parent": []})
        self.assertEqual(["上传.txt"], second_upload["data"]["item"]["path"])
        # The picker authorization is single-use.
        self.assertEqual("operation_failed", self.call("drive_upload", {"path": picked["files"][0]["path"], "parent": []})["error"]["code"])
        bad_token = self.call("drive_upload_register", {"path": str(self.local), "drop_token": "forged"})
        self.assertEqual("operation_failed", bad_token["error"]["code"])
        capability = _authorize_native_drop_paths((str(self.local),))[0]
        registered = self.call("drive_upload_register", capability)["data"]
        conflict = self.call("drive_upload", {"path": registered["path"], "parent": ["课程"]})
        self.assertEqual("conflict", conflict["error"]["code"])
        renamed = self.call("drive_upload", {"path": registered["path"], "parent": ["课程"], "conflict": "rename"})
        self.assertEqual("上传 (1).txt", renamed["data"]["item"]["name"])
        self.assertEqual("operation_failed", self.call("drive_upload", {"path": registered["path"], "parent": [], "conflict": "x"})["error"]["code"])

    def test_upload_capability_cannot_be_used_concurrently(self):
        token = self.call("drive_upload_pick")["data"]["files"][0]["path"]
        entered, proceed = threading.Event(), threading.Event()
        drive = self.bridge._drive()
        original = drive.upload

        def blocking_upload(*args, **kwargs):
            entered.set()
            self.assertTrue(proceed.wait(5))
            return original(*args, **kwargs)

        results = []
        with patch.object(drive, "upload", side_effect=blocking_upload):
            thread = threading.Thread(target=lambda: results.append(self.call("drive_upload", {"path": token, "parent": []})))
            thread.start()
            self.assertTrue(entered.wait(5))
            second = self.call("drive_upload", {"path": token, "parent": ["课程"]})
            proceed.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0]["ok"])
        self.assertFalse(second["ok"])

    def test_upload_rejects_file_or_parent_replaced_after_registration(self):
        target = self.root / "unselected.txt"
        target.write_bytes(b"other")
        token = self.call("drive_upload_pick")["data"]["files"][0]["path"]
        self.local.unlink()
        try:
            self.local.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        result = self.call("drive_upload", {"path": token, "parent": []})
        self.assertFalse(result["ok"])
        self.assertNotIn(("上传.txt",), self.pan.nodes)

        original = self.root / "selected"
        original.mkdir()
        selected = original / "item.txt"
        selected.write_bytes(b"selected")
        other = self.root / "other"
        other.mkdir()
        (other / "item.txt").write_bytes(b"other")
        token = self.bridge._drive_register_upload(str(selected))["path"]
        original.rename(self.root / "moved")
        original.symlink_to(other, target_is_directory=True)
        self.assertFalse(self.call("drive_upload", {"path": token, "parent": []})["ok"])

    def test_upload_uses_verified_handle_after_path_changes(self):
        token = self.call("drive_upload_pick")["data"]["files"][0]["path"]
        target = self.root / "unselected.txt"
        target.write_bytes(b"other")
        drive = self.bridge._drive()
        original_upload = drive.upload

        def swap_then_upload(*args, **kwargs):
            try:
                self.local.unlink()
                self.local.symlink_to(target)
            except OSError:
                # Windows keeps the verified source handle locked.  That
                # prevents replacement and is the secure platform outcome.
                if os.name != "nt":
                    raise
            return original_upload(*args, **kwargs)

        with patch.object(drive, "upload", side_effect=swap_then_upload):
            result = self.call("drive_upload", {"path": token, "parent": []})
        self.assertTrue(result["ok"])
        self.assertEqual(b"payload", self.pan.blobs[("上传.txt",)])

    def test_upload_capability_can_be_released(self):
        token = self.call("drive_upload_pick")["data"]["files"][0]["path"]
        released = self.call("drive_upload_release", {"tokens": [token]})
        self.assertEqual(1, released["data"]["released"])
        self.assertEqual(
            "operation_failed",
            self.call("drive_upload", {"path": token, "parent": []})["error"]["code"],
        )

    def test_folder_upload_keeps_relative_structure(self):
        (self.root / "子目录").mkdir()
        (self.root / "子目录" / "a.md").write_text("a")
        picked = self.call("drive_upload_pick_folder")["data"]
        entry = next(file for file in picked["files"] if file["name"] == "a.md")
        self.assertEqual([self.root.name, "子目录"], entry["relative_dir"])
        result = self.call("drive_upload", {"path": entry["path"], "parent": ["课程"], "relative_dir": entry["relative_dir"]})
        self.assertEqual(["课程", self.root.name, "子目录", "a.md"], result["data"]["item"]["path"])

    def test_download_cancel_before_or_during_save_picker(self):
        transfer_id = "download-cancel-1"
        path = ["课程", "第1周.pdf"]
        self.assertTrue(self.call("drive_prepare_download", {"path": path, "transfer_id": transfer_id})["ok"])
        self.assertTrue(self.call("drive_cancel", {"transfer_id": transfer_id})["data"]["cancelled"])
        picker = Mock(return_value=str(self.saved))
        self.bridge._save_file_picker = picker
        self.assertEqual("cancelled", self.call("drive_download", {"path": path, "transfer_id": transfer_id})["error"]["code"])
        picker.assert_not_called()
        self.assertFalse(self.saved.exists())

        entered, proceed = threading.Event(), threading.Event()
        def wait_picker(_name):
            entered.set()
            self.assertTrue(proceed.wait(5))
            return str(self.saved)
        self.bridge._save_file_picker = wait_picker
        results = []
        self.assertTrue(self.call("drive_prepare_download", {"path": path, "transfer_id": "download-cancel-2"})["ok"])
        thread = threading.Thread(target=lambda: results.append(self.call("drive_download", {"path": path, "transfer_id": "download-cancel-2"})))
        thread.start()
        self.assertTrue(entered.wait(5))
        self.assertTrue(self.call("drive_cancel", {"transfer_id": "download-cancel-2"})["data"]["cancelled"])
        proceed.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual("cancelled", results[0]["error"]["code"])
        self.assertFalse(self.saved.exists())

    def test_prepared_download_is_bound_to_path_and_cannot_be_reused(self):
        payload = {"path": ["课程", "第1周.pdf"], "transfer_id": "download-bound"}
        self.assertTrue(self.call("drive_prepare_download", payload)["ok"])
        self.assertFalse(self.call("drive_prepare_download", payload)["ok"])
        self.assertFalse(self.call("drive_download", {**payload, "path": ["other.pdf"]})["ok"])
        self.assertTrue(self.call("drive_download", payload)["ok"])
        self.assertFalse(self.call("drive_download", payload)["ok"])

    def test_download_and_reveal(self):
        self.assertTrue(self.call("drive_prepare_download", {"path": ["课程", "第1周.pdf"], "transfer_id": "dl-000001"})["ok"])
        result = self.call("drive_download", {"path": ["课程", "第1周.pdf"], "transfer_id": "dl-000001"})
        self.assertEqual("downloaded", result["data"]["status"])
        self.assertNotIn(str(self.root), str(result))
        self.assertEqual(b"hello", self.saved.read_bytes())
        revealed = self.call("drive_reveal_download", {"transfer_id": "dl-000001"})
        self.assertEqual("revealed", revealed["data"]["status"])
        self.runner.assert_called_once()
        self.assertEqual("operation_failed", self.call("drive_reveal_download", {"transfer_id": "unknown-01"})["error"]["code"])
        transfers = self.call("drive_transfers")["data"]["items"]
        self.assertEqual("done", transfers[0]["status"])
        self.assertFalse(self.call("drive_cancel", {"transfer_id": "dl-000001"})["data"]["cancelled"])
        cancelled = DesktopBridge(Mock(), FakeLearning(self.pan), save_file_picker=lambda name: None)
        self.assertEqual("cancelled", cancelled.invoke("drive_download", {"path": ["课程", "第1周.pdf"]})["data"]["status"])

    def test_save_target_symlink_does_not_overwrite_its_destination(self):
        victim = self.root / "victim.pdf"
        victim.write_bytes(b"original")
        try:
            self.saved.symlink_to(victim)
        except OSError:
            self.skipTest("当前环境不允许创建符号链接")
        result = self.call("drive_download", {"path": ["课程", "第1周.pdf"]})
        self.assertFalse(result["ok"])
        self.assertIn("同名文件", result["error"]["message"])
        self.assertEqual(b"original", victim.read_bytes())
        self.assertTrue(self.saved.is_symlink())

    def test_missing_learning_service_or_pickers(self):
        bare = DesktopBridge(Mock())
        self.assertEqual("operation_failed", bare.invoke("drive_space")["error"]["code"])
        self.assertEqual("operation_failed", bare.invoke("drive_upload_pick")["error"]["code"])


if __name__ == "__main__":
    unittest.main()
