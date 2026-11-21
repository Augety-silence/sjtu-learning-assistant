import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { getProviderUsage, type ProviderUsageSummary } from "@/lib/api";

export function TokenUsageDashboard() {
  const [data, setData] = useState<ProviderUsageSummary | null>(null);
  const [period, setPeriod] = useState<"today" | "week" | "all">("today");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    let pending = false;
    const refresh = async () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      try {
        const next = await getProviderUsage();
        if (active) {
          setData(next);
          setError("");
        }
      } catch {
        if (active) setError("用量读取失败");
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [attempt]);
  const value = data?.periods[period];
  return (
    <section
      className="settings-panel token-usage-panel"
      aria-labelledby="token-usage-title"
    >
      <div className="section-header">
        <h3 id="token-usage-title">Token 用量</h3>
        <label className="token-usage-period">
          统计范围
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value as typeof period)}
          >
            <option value="today">今日</option>
            <option value="week">近 7 天</option>
            <option value="all">升级以来</option>
          </select>
        </label>
      </div>
      <dl className="token-usage-metrics">
        {(
          [
            ["input", "输入"],
            ["output", "输出"],
            ["cache", "缓存命中"],
            ["total", "总量"],
          ] as const
        ).map(([key, label]) => {
          const metric = value?.metrics[key];
          return (
            <div key={key}>
              <dt>{label}</dt>
              <dd>
                {!value
                  ? "—"
                  : value.calls === 0
                    ? "0"
                    : metric?.tokens == null
                      ? "未报告"
                      : metric.tokens.toLocaleString("zh-CN")}
              </dd>
              {metric && metric.missing_calls > 0 && (
                <small>{metric.missing_calls} 次未报告</small>
              )}
            </div>
          );
        })}
      </dl>
      <p className="token-usage-note">
        {value ? `${value.calls} 次请求 · ` : ""}
        仅本机升级后的接口计数，包含聊天、课件和字幕编译；缓存属于输入，不重复相加。不设每日调用上限。
      </p>
      {error && (
        <div className="token-usage-error" role="status">
          {error}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setAttempt((v) => v + 1)}
          >
            重试
          </Button>
        </div>
      )}
    </section>
  );
}
