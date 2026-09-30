from __future__ import annotations

import unittest

from desktop_app import DesktopBridge


class FakeService:
    def transcript_batch_start(self, course_id, source_ids):
        return {"id":"b-1","course_id":course_id,"source_ids":source_ids}

    def transcript_batch_get(self, batch_id):
        return {"id":batch_id}

    def transcript_jobs(self, course_id=None):
        return {"items":[],"course_id":course_id}

    def transcript_retry(self, job_id):
        return {"id":"b-2","job_id":object() and job_id}

    def transcript_cancel(self, *, batch_id=None, job_id=None):
        return {"status":"cancelled","batch_id":batch_id,"job_id":job_id}

    def transcript_artifacts(self, job_id):
        return {"items":[],"job_id":job_id}

    def transcript_artifact_read(self, artifact_id):
        return {"id":artifact_id,"content":"ok"}

    def transcript_artifact_reveal(self, artifact_id):
        return {"id":artifact_id,"status":"revealed"}


class TranscriptBridgeTests(unittest.TestCase):
    def test_allowlist_and_strict_payloads(self):
        bridge = DesktopBridge(FakeService())
        expected = {"transcript_batch_start","transcript_batch_get","transcript_jobs","transcript_retry","transcript_cancel","transcript_artifacts","transcript_artifact_read","transcript_artifact_reveal"}
        self.assertTrue(expected.issubset(bridge._handlers))
        started = bridge.invoke("transcript_batch_start",{"course_id":12,"source_ids":["sjtu-video:12:99"]})
        self.assertTrue(started["ok"])
        for payload in ({"course_id":12},{"course_id":12,"source_ids":[]},{"course_id":12,"source_ids":["sjtu-video:13:99"]},{"course_id":12,"source_ids":["sjtu-video:12:99"],"endpoint":"https://evil"}):
            self.assertFalse(bridge.invoke("transcript_batch_start",payload)["ok"])
        self.assertFalse(bridge.invoke("transcript_artifact_read",{"artifact_id":"../secret"})["ok"])


if __name__ == "__main__":
    unittest.main()
