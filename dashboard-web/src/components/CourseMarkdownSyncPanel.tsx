import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { getCourseMarkdownStatus, updateSettings } from "@/lib/api";
import type { CourseMarkdownStatus } from "@/lib/types";

const labels: Record<string, string> = {
  disabled: "未开启",
  needs_ai: "等待 AI 配置",
  running: "自动转换中",
  completed: "已完成当前同步",
  completed_with_warnings: "部分课件需要处理",
  budget_paused: "今日额度已用完，次日自动续编",
  paused: "已暂停",
  blocked: "需要检查配置或输出目录",
};

export function CourseMarkdownSyncPanel() {
  const [state, setState] = useState<CourseMarkdownStatus | null>(null);
  const [limit, setLimit] = useState(100);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let initialized = false;
    const load = () =>
      getCourseMarkdownStatus()
        .then((value) => {
          if (active) {
            setState(value);
            setError("");
            if (!initialized) {
              setLimit(value.daily_limit);
              initialized = true;
            }
          }
        })
        .catch((reason: unknown) => {
          if (active)
            setError(
              reason instanceof Error ? reason.message : "自动同步状态读取失败",
            );
        });
    void load();
    const timer = window.setInterval(() => void load(), 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);
  const save = async (enabled: boolean) => {
    setBusy(true);
    try {
      await updateSettings(
        enabled
          ? {
              course_markdown_enabled: true,
              course_markdown_daily_calls: limit,
            }
          : { course_markdown_enabled: false },
      );
      setState(await getCourseMarkdownStatus());
      setError("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "自动同步设置保存失败",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="knowledge-config" aria-labelledby="course-md-title">
      <div className="section-header">
        <div>
          <h2 id="course-md-title">课件自动同步为 Markdown</h2>
          <p>
            无需选目录或运行命令。开启后，App
            启动补扫已有课件，下载或同步完成后自动增量转换。
          </p>
        </div>
      </div>
      <p>
        会将已下载 PPTX / PDF 的逐页图文发送到已配置 AI 接口。每份课件对应一个
        MD，保留课程目录、原文和页图；人工编辑不覆盖。旧 PPT 需安全导出 PDF。App
        退出时暂停，重开后续编。
      </p>
      {state && (
        <>
          <p>
            输出目录：<code>{state.output_root}</code>
          </p>
          <p role="status">
            {labels[state.status] ?? state.status} · 文本 MD 就绪{" "}
            {state.text_ready ?? 0}/{state.total}份 · 多模态完成{" "}
            {state.completed} · 待续编 {state.pending} · 异常 {state.failed} ·
            今日调用 {state.calls_today}/{state.daily_limit}
          </p>
        </>
      )}
      <label>
        每日页面调用上限（失败也计入）{" "}
        <input
          type="number"
          min={1}
          max={1000}
          value={limit}
          disabled={busy}
          onChange={(event) => setLimit(Number(event.target.value))}
        />
      </label>
      <div className="knowledge-scan-row">
        <Button
          disabled={
            !state ||
            busy ||
            !Number.isInteger(limit) ||
            limit < 1 ||
            limit > 1000
          }
          onClick={() => void save(true)}
        >
          {state?.enabled ? "保存上限并继续自动同步" : "开启课件自动同步"}
        </Button>
        <Button
          variant="outline"
          disabled={!state?.enabled || busy}
          onClick={() => void save(false)}
        >
          暂停自动同步
        </Button>
      </div>
      {error && <p role="alert">{error}</p>}
      {state && state.issues.length > 0 && (
        <details>
          <summary>待处理课件（最多显示 20 项）</summary>
          <ul>
            {state.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
