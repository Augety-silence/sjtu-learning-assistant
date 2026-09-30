from __future__ import annotations

from desktop_app import DesktopBridge


class FakeArchiveDashboard:
    def __init__(self):
        self.calls = []

    def archive_start(self, path, key):
        self.calls.append(("start", path, key))
        return {"status": "completed"}

    def archive_list(self, limit, offset, **kwargs):
        return {"limit": limit, "offset": offset, **kwargs}

    def archive_detail(self, entry_id):
        return {"id": entry_id}

    def archive_retry(self, job_id):
        return {"id": job_id}

    def archive_jobs(self, limit, status):
        return {"limit": limit, "status": status}

    def archive_job_events(self, job_id):
        return {"id": job_id}

    def archive_authorize_root(self):
        return {"id": "root"}

    def restore_plan(self, entry_id, version_id, mode, root_id):
        self.calls.append(("plan", entry_id, version_id, mode, root_id))
        return {"job": {"id": "restore"}}

    def restore_execute(self, job_id, policy, confirm):
        self.calls.append(("execute", job_id, policy, confirm))
        return {"status": "completed"}


def test_archive_bridge_uses_native_picker_and_allowlisted_commands():
    service = FakeArchiveDashboard()
    bridge = DesktopBridge(service, file_picker=lambda: "/tmp/picked.txt")
    response = bridge.invoke("archive_start", {"idempotency_key": "once"})
    assert response["ok"] is True
    assert service.calls == [("start", "/tmp/picked.txt", "once")]
    listed = bridge.invoke("archive_list", {"limit": 10, "offset": 2, "query": "pdf", "sort": "name_asc"})
    assert listed["ok"] is True
    assert listed["data"]["query"] == "pdf"
    assert bridge.invoke("archive_authorize_root", {})["ok"] is True


def test_restore_bridge_rejects_client_target_path_and_validates_confirmation():
    service = FakeArchiveDashboard()
    bridge = DesktopBridge(service)
    rejected = bridge.invoke("restore_plan", {"entry_id": "entry", "target": "/tmp/evil"})
    assert rejected["ok"] is False
    assert service.calls == []
    planned = bridge.invoke(
        "restore_plan",
        {"entry_id": "entry", "mode": "choose_location", "authorized_root_id": "root"},
    )
    assert planned["ok"] is True
    invalid = bridge.invoke(
        "restore_execute",
        {"job_id": "restore", "conflict_policy": "overwrite", "confirm_create_dirs": "yes"},
    )
    assert invalid["ok"] is False
