import { useEffect, useRef, useState } from "react";
import {
  type CourseScope,
  CourseScopePicker,
} from "@/components/CourseScopePicker";
import { MaterialsView } from "@/components/MaterialsView";
import { Button } from "@/components/ui/Button";
import { invoke } from "@/lib/api";

type HistoryJob = {
  status: string;
  completed: number;
  total: number;
  message?: string;
  term_id?: string;
  course_ids?: string[];
  result?: { downloaded: number; download_failed: number; issues: unknown[] };
};
const statusLabels: Record<string, string> = {
  idle: "未开始",
  running: "同步中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  interrupted: "未确认完成",
};

export function CourseMaterialsView() {
  const [scope, setScope] = useState<CourseScope>({ termId: "", courseId: "" });
  const [job, setJob] = useState<HistoryJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const pendingStart = useRef(false);
  const previousStatus = useRef("");
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (pendingStart.current) {
        timer = setTimeout(() => void poll(), 2000);
        return;
      }
      const request = ++generation.current;
      try {
        const result = await invoke<HistoryJob>("course_history_status");
        if (active && request === generation.current) {
          setJob(result);
          if (
            previousStatus.current === "running" &&
            result.status !== "running"
          )
            setRevision((value) => value + 1);
          previousStatus.current = result.status;
        }
      } catch (reason) {
        if (active && request === generation.current)
          setError(reason instanceof Error ? reason.message : "状态读取失败");
      }
      if (active) timer = setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => {
      active = false;
      ++generation.current;
      clearTimeout(timer);
    };
  }, []);
  const start = async (download: boolean) => {
    const request = ++generation.current;
    pendingStart.current = true;
    setStarting(true);
    setError("");
    try {
      const result = await invoke<HistoryJob>("course_history_start", {
        term_id: scope.termId,
        ...(scope.courseId ? { course_ids: [scope.courseId] } : {}),
        download,
      });
      if (request === generation.current) {
        setJob(result);
        previousStatus.current = result.status;
      }
    } catch (reason) {
      if (request === generation.current)
        setError(reason instanceof Error ? reason.message : "范围同步失败");
    } finally {
      pendingStart.current = false;
      if (request === generation.current) setStarting(false);
    }
  };
  const busy = starting || job?.status === "running";
  return (
    <>
      <section className="course-history-panel" aria-label="历史课程同步">
        <CourseScopePicker scope={scope} onChange={setScope} />
        <div className="course-history-actions">
          <Button
            disabled={busy || !scope.termId}
            onClick={() => void start(false)}
          >
            同步所选资料目录
          </Button>
          <Button
            variant="outline"
            disabled={busy || !scope.termId}
            onClick={() => void start(true)}
          >
            同步并下载所选课件
          </Button>
        </div>
        <p>
          按学期、课程读取资料，不改默认待办范围。下载后的课件将进入现有自动
          Markdown 流程，仍受编译开关和额度限制。
        </p>
        {job && job.status !== "idle" && (
          <p role="status">
            {statusLabels[job.status] ?? job.status} · {job.completed}/
            {job.total} 门（任务固定范围：{job.course_ids?.length ?? 0} 门） ·{" "}
            {job.message}
            {job.result
              ? ` · 下载 ${job.result.downloaded}，下载失败 ${job.result.download_failed}，受限资源 ${job.result.issues.length}`
              : ""}
          </p>
        )}
        {error && <p role="alert">{error}</p>}
      </section>
      <MaterialsView
        key={`${scope.termId}:${scope.courseId}:${revision}`}
        termId={scope.termId}
        courseId={scope.courseId}
      />
    </>
  );
}
