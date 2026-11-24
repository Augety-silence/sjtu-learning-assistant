import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import {
  getRecordingASRJobs,
  getRecordingASRStatus,
  pauseRecordingASR,
  resumeRecordingASR,
  retryRecordingASR,
  scanRecordingASR,
  startRecordingASR,
} from "@/lib/api";
import type {
  RecordingASRJobs,
  RecordingASRStatus,
  SettingsStatus,
} from "@/lib/types";

const labels: Record<string, string> = {
  completed: "MD完成/人工未校对",
  skipped_source_text: "已有字幕或原文，已保留",
  checking_source: "检查已有字幕和原文",
  publishing: "保存完整原文 MD",
  asr_completed: "识别已保存，等待生成MD",
  outcome_unknown: "响应未知，禁止自动重发",
  blocked_account: "账户余额不足，需手动重试",
  user_edit_protected: "人工修改已保护",
  checkpoint_error: "检查点不可读取，未重置",
  total: "录播总数",
  requests: "实际请求数",
  queued: "等待处理",
  pending: "等待处理",
  downloading: "下载音频",
  extracting: "提取音频",
  transcribing: "语音识别中",
  paused: "已暂停",
  failed: "失败，等待手动重试",
  response_unknown: "响应未知，禁止自动重发",
  skipped_caption: "已有字幕，已保留",
  skipped_completed: "已有完成稿，已保留",
};
export function RecordingASRPanel({
  settings,
  active = true,
  disabled = false,
}: {
  settings: SettingsStatus;
  active?: boolean;
  disabled?: boolean;
}) {
  const [state, setState] = useState<RecordingASRStatus | null>(null);
  const [jobs, setJobs] = useState<RecordingASRJobs>({ items: [] });
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await getRecordingASRStatus();
        const list = await getRecordingASRJobs();
        if (!next || !list || !Array.isArray(list.items))
          throw new Error("录播转写进度格式不正确，请刷新后重试");
        if (!cancelled) {
          setState(next);
          setJobs(list);
        }
      } catch (reason) {
        if (!cancelled)
          setError(reason instanceof Error ? reason.message : "进度读取失败");
      } finally {
        if (!cancelled) timer = setTimeout(() => void poll(), 3000);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [active, revision]);
  const run = async (
    action: string,
    task: () => Promise<RecordingASRStatus>,
  ) => {
    setBusy(action);
    setError("");
    try {
      await task();
      setRevision((n) => n + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "录播转写操作失败");
    } finally {
      setBusy("");
    }
  };
  const enabled = state?.enabled ?? settings.recording_asr_enabled ?? false;
  const paused = state?.paused ?? settings.recording_asr_paused ?? true;
  const coverage = state?.coverage;
  const unavailable = disabled || Boolean(busy) || !settings.asr_key_saved;
  return (
    <section aria-labelledby="recording-asr-title" style={{ marginTop: 20 }}>
      <h4 id="recording-asr-title">全部未完成录播 → 完整原文 MD</h4>
      <p>
        保留现有字幕、完成稿和付费检查点。只需语音密钥，不需要聊天密钥。无时间戳原文不进入字幕轨道或时间轴；MD完成不代表
        AI 审阅或人工校对通过。
      </p>
      {!enabled && (
        <label>
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            disabled={Boolean(busy)}
          />
          我同意将全部可访问课程中未完成的录播音频发送至已保存的语音接口，可能产生费用；授权和暂停状态保存在本机。
        </label>
      )}
      <div className="settings-actions">
        <Button
          type="button"
          variant="outline"
          disabled={Boolean(busy)}
          onClick={() => void run("scan", scanRecordingASR)}
        >
          扫描录播
        </Button>
        {!enabled ? (
          <Button
            type="button"
            disabled={unavailable || !consent}
            onClick={() => {
              if (
                window.confirm(
                  `将全部未完成录播音频发送到 ${settings.asr_endpoint}，模型 ${settings.asr_model}，可能产生费用。授权后持续处理并在下次启动继续，直到暂停。确认开始？`,
                )
              )
                void run("start", () => startRecordingASR(true));
            }}
          >
            授权并开始
          </Button>
        ) : paused ? (
          <Button
            type="button"
            disabled={unavailable}
            onClick={() => {
              if (
                window.confirm(
                  `继续已授权范围，使用当前接口 ${settings.asr_endpoint} 和模型 ${settings.asr_model}；保留检查点，未知响应不自动重发。继续？`,
                )
              )
                void run("resume", resumeRecordingASR);
            }}
          >
            继续转写
          </Button>
        ) : (
          <Button
            type="button"
            variant="outline"
            disabled={Boolean(busy)}
            onClick={() => void run("pause", pauseRecordingASR)}
          >
            暂停转写
          </Button>
        )}
      </div>
      {!settings.asr_key_saved && <p>请先保存自己的语音 API Key。</p>}
      <div role="status" aria-live="polite">
        <p>
          {state?.scanning ? "扫描中… " : ""}
          {state?.draining
            ? "正在保存已发送的语音响应，排空后再继续"
            : state?.drain_error
              ? "排空存储失败，已暂停；不会自动重发"
              : enabled
                ? paused
                  ? "已暂停"
                  : state?.account_blocked
                    ? "账户余额不足，处理已阻止；充值后仅手动重试失败项"
                    : "已授权，处理中"
                : "尚未授权批量外发"}
          {state?.busy &&
            paused &&
            "；正在保存已发送请求的响应，暂停不丢弃付费结果。"}
        </p>
        {state?.counts && (
          <p>
            {Object.entries(state.counts)
              .map(([kind, count]) => `${labels[kind] ?? kind} ${count}`)
              .join(" · ")}
          </p>
        )}
        {coverage && (
          <p>
            课程扫描：{coverage.scanned_courses}/
            {coverage.total_courses ?? "未知"}；不可访问{" "}
            {coverage.inaccessible_courses.length}；无有效来源{" "}
            {coverage.missing_sources}。
            {coverage.status !== "complete" &&
              "覆盖范围尚未确认完整，不代表全部课程已完成。"}
          </p>
        )}
      </div>
      {coverage && coverage.inaccessible_courses.length > 0 && (
        <details>
          <summary>未覆盖的课程</summary>
          <ul>
            {coverage.inaccessible_courses.map((course) => (
              <li key={course.course_id}>
                {course.course_name}：{course.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      {(error || state?.error) && <p role="alert">{error || state?.error}</p>}
      <details>
        <summary>任务进度与有界手动重试</summary>
        {jobs.truncated && (
          <p>仅展示前100项任务；总体进度以上方持久化计数为准。</p>
        )}
        <ul>
          {jobs.items.map((job) => (
            <li
              key={job.source_id}
              style={{ marginBottom: 10, overflowWrap: "anywhere" }}
            >
              <strong>
                {job.course_name} · {job.title || "未命名录播"}
              </strong>{" "}
              — {labels[job.status] ?? job.status_label ?? job.status}
              {job.chunks_total != null &&
                `；分片 ${job.chunks_completed ?? 0}/${job.chunks_total}`}
              {job.error && <p>{job.error}</p>}
              {(job.retryable ||
                job.status === "failed" ||
                job.status.includes("unknown")) && (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={unavailable || !enabled}
                  onClick={() => {
                    const unknown = job.status.includes("unknown");
                    if (
                      window.confirm(
                        unknown
                          ? "上次响应未知，可能已经扣费。重发未确认分片可能重复扣费；确认一次有界重试？"
                          : "保留响应和检查点，仅重试未完成部分；受服务预算限制。继续？",
                      )
                    )
                      void run("retry", () =>
                        retryRecordingASR(job.source_id, unknown),
                      );
                  }}
                >
                  重试未完成部分
                </Button>
              )}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
