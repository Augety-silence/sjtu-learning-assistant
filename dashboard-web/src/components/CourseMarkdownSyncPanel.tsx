import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
import { getCourseMarkdownStatus, updateSettings } from "@/lib/api";
import type { CourseMarkdownStatus } from "@/lib/types";

const labels: Record<string, string> = {
  disabled: "未开启",
  needs_ai: "等待 AI 配置",
  running: "自动转换中",
  completed: "已完成当前同步",
  completed_with_warnings: "部分课件需要处理",
  paused: "已暂停",
  blocked: "需要检查配置或输出目录",
};

export function CourseMarkdownSyncPanel() {
  const [state, setState] = useState<CourseMarkdownStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = () =>
      getCourseMarkdownStatus()
        .then((value) => {
          if (active) {
            setState(value);
            setError("");
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
              course_markdown_daily_calls: 0,
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
        <div className="heading-with-info">
          <h2 id="course-md-title">课件自动同步</h2>
          <InfoTooltip label="课件同步说明">
            自动补扫已下载课件并增量生成
            Markdown，按课程目录保存，保留人工修改。退出后暂停，重新打开后续编；没有每日调用上限，仍遵守接口限速。
            {state?.output_root && <>输出目录：{state.output_root}</>}
          </InfoTooltip>
        </div>
      </div>
      {!state?.enabled && <p>开启后，课件图文将发送至所配置的 AI 接口。</p>}
      {state && (
        <>
          <p role="status">
            {labels[state.status] ?? state.status} · {state.completed}/
            {state.total}
            {state.pending > 0 && ` · 待续编 ${state.pending}`}
            {state.failed > 0 && ` · 异常 ${state.failed}`}
          </p>
        </>
      )}
      <div className="knowledge-scan-row">
        <Button disabled={!state || busy} onClick={() => void save(true)}>
          {state?.enabled ? "继续自动同步" : "开启课件自动同步"}
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
          <summary>待处理课件</summary>
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
