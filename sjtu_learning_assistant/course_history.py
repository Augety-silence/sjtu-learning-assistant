"""显式范围的历史课程导入；不改变默认同步范围，不发送历史通知。"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.course_scope import current_course_ids, select_scope, term_identity, validate_scope
from sjtu_learning_assistant.models import Course, SyncState
from sjtu_learning_assistant.repository import persist_course_catalog
from sync_runner import SyncRunner


class CourseHistoryError(RuntimeError):
    pass


class CourseHistoryService:
    def __init__(self, engine, *, fetch_rows, synchronize, sync_lock, on_finished=lambda: None, lock_path=None):
        self.engine = engine
        self.fetch_rows = fetch_rows
        self.synchronize = synchronize
        self.sync_lock = sync_lock
        self.on_finished = on_finished
        self._mutex = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._runner = SyncRunner((), **({"lock_path": lock_path} if lock_path is not None else {}))
        self._job = self._load()
        if self._job.get("status") == "running":
            # 先前进程未确认结束；只恢复显示，不擅自重跑下载/模型调用。
            self._job.update(status="interrupted", message="上次导入未确认完成，请重新同步所选范围。")

    def _load(self):
        with Session(self.engine) as session:
            state = session.scalar(select(SyncState).where(SyncState.source == "canvas", SyncState.resource == "course_history_job"))
            try:
                value = json.loads(state.cursor) if state and state.cursor and len(state.cursor) <= 256 * 1024 else None
                if type(value) is dict and value.get("version") == 1:
                    return value
            except (ValueError, TypeError, RecursionError):
                pass
        return {"version": 1, "status": "idle", "completed": 0, "total": 0}

    def _save(self):
        with Session(self.engine) as session, session.begin():
            state = session.scalar(select(SyncState).where(SyncState.source == "canvas", SyncState.resource == "course_history_job"))
            if state is None:
                state = SyncState(source="canvas", resource="course_history_job")
                session.add(state)
            state.cursor = json.dumps(self._job, ensure_ascii=False)
            state.status = self._job["status"]
            state.last_sync_at = datetime.now(timezone.utc)

    def status(self):
        with self._mutex:
            return json.loads(json.dumps(self._job))

    def catalog(self, refresh=False):
        if type(refresh) is not bool:
            raise CourseHistoryError("刷新参数无效。")
        if refresh:
            if self._stop.is_set() or not self.sync_lock.acquire(blocking=False):
                raise CourseHistoryError("同步进行中，请完成后刷新目录。")
            fd = None
            try:
                fd = self._runner._acquire_lock()
                if fd is None:
                    raise CourseHistoryError("后台同步进行中，请稍后刷新。")
                rows = self.fetch_rows()
                persist_course_catalog(self.engine, rows)
            except CourseHistoryError:
                raise
            except Exception:
                raise CourseHistoryError("课程目录刷新失败；缓存未替换。请检查 Canvas 凭据与网络后重试。") from None
            finally:
                if fd is not None:
                    self._runner._release_lock(fd)
                self.sync_lock.release()
        with Session(self.engine) as session:
            active = current_course_ids(session)
            courses = list(session.scalars(select(Course).order_by(Course.term_name.desc(), Course.name, Course.source_id)))
            state = session.scalar(select(SyncState).where(SyncState.source == "canvas", SyncState.resource == "course_catalog"))
            terms = {}
            for course in courses:
                key, label = term_identity(course.raw_data or {}, course.term_name)
                group = terms.setdefault(key, {"id": key, "name": label, "courses": []})
                restricted = bool((course.raw_data or {}).get("access_restricted_by_date"))
                group["courses"].append({"id": course.source_id, "name": course.name, "code": course.course_code,
                                         "current": course.id in active if active is not None else False,
                                         "access": "restricted" if restricted else "available"})
            return {"terms": list(terms.values()), "count": len(courses), "refreshed_at": state.last_success_at.isoformat() if state and state.last_success_at else None,
                    "cached": not refresh, "complete": bool(state and state.last_success_at)}

    def start(self, term_id="", course_ids=None, download=False):
        if type(download) is not bool:
            raise CourseHistoryError("下载选项无效。")
        try:
            term, ids = validate_scope(term_id, course_ids)
            if not term and not ids:
                raise ValueError("请选择学期或课程。")
        except ValueError as exc:
            raise CourseHistoryError(str(exc)) from None
        with self._mutex:
            if self._stop.is_set():
                raise CourseHistoryError("应用正在关闭。")
            if not self.sync_lock.acquire(blocking=False):
                raise CourseHistoryError("已有同步任务，请等待完成。")
            fd = None
            try:
                fd = self._runner._acquire_lock()
                if fd is None:
                    raise CourseHistoryError("后台同步进行中，请稍后重试。")
                self._job = {"version": 1, "status": "running", "term_id": term, "course_ids": list(ids),
                             "download": download, "completed": 0, "total": 0, "message": "重新核验当前账号课程范围"}
                self._save()
                self._thread = threading.Thread(target=self._run, args=(fd,), name="sjtu-course-history", daemon=True)
                self._thread.start()
                return self.status()
            except Exception:
                if fd is not None:
                    self._runner._release_lock(fd)
                self.sync_lock.release()
                raise

    def _progress(self, completed, total, message):
        if self._stop.is_set():
            raise InterruptedError()
        with self._mutex:
            self._job.update(completed=completed, total=total, message=message)
            self._save()

    def _run(self, fd):
        try:
            # 缓存及 renderer 传入的 ID 不是授权证据；必须重新获取账号可见目录。
            job = self.status()
            rows = select_scope(self.fetch_rows(), job["term_id"], job["course_ids"] or None)
            self._progress(0, len(rows), "同步课程资料")
            with self._mutex:
                self._job["course_ids"] = [str(row["id"]) for row in rows]
            result = self.synchronize(rows, job["download"], self._progress)
            if self._stop.is_set():
                raise InterruptedError()
            with self._mutex:
                self._job.update(result=result, status="partial" if result.get("issues") or result.get("download_failed") else "completed",
                                 completed=len(rows), total=len(rows), message="范围导入结束；受限或失败资源可稍后重试")
                self._save()
            try:
                self.on_finished()
            except Exception:
                # 资料导入已完成；编译唤醒失败不回写成数据导入失败。
                pass
        except InterruptedError:
            with self._mutex:
                self._job.update(status="interrupted", message="应用退出，导入未确认完成；重新同步可安全对账。")
                self._save()
        except Exception:
            with self._mutex:
                self._job.update(status="failed", message="历史同步失败。已保存资料保留；请检查目录权限、凭据和网络后重试。")
                self._save()
        finally:
            self._runner._release_lock(fd)
            self.sync_lock.release()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
