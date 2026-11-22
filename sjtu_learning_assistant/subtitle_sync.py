"""Discover all authorized recordings independently of playback and AI work."""
from __future__ import annotations

import threading
import logging
from typing import Callable

LOGGER = logging.getLogger(__name__)


class SubtitleSync:
    def __init__(self, courses: Callable, videos: Callable, transcripts: Callable, *, interval: float = 3600):
        self.courses, self.videos, self.transcripts = courses, videos, transcripts
        self.interval = interval
        self._stop, self._wake = threading.Event(), threading.Event()
        self._guard = threading.RLock()
        self._thread = None
        self._retry = False
        self._state = {"status": "idle", "courses": 0, "checked": 0, "recordings": 0, "queued": 0, "unavailable": 0}

    def start(self):
        with self._guard:
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._loop, name="subtitle-discovery", daemon=True)
                self._thread.start()

    def notify(self, *, retry=False):
        with self._guard:
            self._retry = self._retry or retry
        self._wake.set()

    def status(self):
        with self._guard:
            return dict(self._state)

    def _set(self, **values):
        with self._guard:
            self._state.update(values)

    def _loop(self):
        while not self._stop.is_set():
            self._wake.clear()
            self.run_once()
            self._wake.wait(self.interval)

    def run_once(self):
        with self._guard:
            retry, self._retry = self._retry, False
        self._set(status="discovering", checked=0, recordings=0, queued=0, unavailable=0)
        try:
            courses = self.courses()
            self._set(courses=len(courses))
            transcripts = self.transcripts()
            for course_id, name in courses:
                if self._stop.is_set():
                    return
                try:
                    videos = self.videos(course_id, name)
                    videos = [{**video, "title": video.get("title") or video.get("name") or "课程录像"} for video in videos]
                    queued = transcripts.prefetch(course_id=course_id, course_name=name, videos=videos, retry=retry)
                    state = self.status()
                    self._set(recordings=state["recordings"] + len(videos), queued=state["queued"] + queued)
                except Exception:
                    # One inaccessible/archived course must not block other courses.
                    self._set(unavailable=self.status()["unavailable"] + 1)
                self._set(checked=self.status()["checked"] + 1)
            self._set(status="queued" if self.status()["queued"] else "idle")
            state = self.status()
            LOGGER.info("字幕预同步扫描结束；courses=%s recordings=%s queued=%s unavailable=%s",
                        state["courses"], state["recordings"], state["queued"], state["unavailable"])
        except Exception:
            self._set(status="unavailable")

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
