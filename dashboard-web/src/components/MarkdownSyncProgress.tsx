import { Activity, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
import { getMarkdownSyncProgress } from "@/lib/api";
import type {
  MarkdownProgressGroup,
  MarkdownSyncProgressState,
} from "@/lib/types";
import "./markdown-sync-progress.css";

const reasons: Record<string, string> = {
  timeout: "请求超时",
  network: "网络异常",
  rate_limit: "接口限流",
  credential: "检查 AI 凭据",
  manifest: "检查同步记录",
  source: "来源不可用",
  conversion: "基础转换失败",
  ai_validation: "总结未通过校验",
  response_format: "模型响应格式异常",
  user_modified: "已保留人工修改",
};

function ProgressRow({
  name,
  group,
}: {
  name: string;
  group: MarkdownProgressGroup;
}) {
  return (
    <section className="markdown-progress-row" aria-label={name}>
      <div className="markdown-progress-heading">
        <h3>{name}</h3>
        <span>
          {group.completed} / {group.total}
        </span>
      </div>
      {group.total > 0 ? (
        <progress
          aria-label={`${name}基础 MD 转换进度`}
          value={group.completed}
          max={group.total}
        />
      ) : (
        <p>{name === "课件" ? "暂无已下载课件" : "暂无已保存字幕"}</p>
      )}
      <dl>
        <div>
          <dt>待转换</dt>
          <dd>{group.pending}</dd>
        </div>
        <div>
          <dt>异常</dt>
          <dd>{group.failed}</dd>
        </div>
        <div>
          <dt>AI 总结</dt>
          <dd>
            {group.ai_completed} / {group.total}
          </dd>
        </div>
      </dl>
      {(group.unavailable ?? 0) > 0 && (
        <div className="heading-with-info">
          <span>暂无字幕 {group.unavailable}</span>
          <InfoTooltip label="暂无字幕说明">
            学校暂未提供这些录像的字幕。自动低频复查，不调用模型，也不计入转换失败。
          </InfoTooltip>
        </div>
      )}
      {group.truncated && <p role="status">已达扫描范围上限，统计不完整</p>}
      {group.issues.length > 0 && (
        <details>
          <summary>
            查看异常{group.ai_failed > 0 ? ` · 总结 ${group.ai_failed}` : ""}
          </summary>
          <ul>
            {group.issues.map((item, index) => (
              <li key={`${item.name}-${item.code}-${index}`}>
                <span title={item.name}>{item.name}</span>
                <span>{reasons[item.code] ?? "需要检查"}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export function MarkdownSyncProgress({
  onNavigate,
}: {
  onNavigate: (view: "knowledge" | "settings") => void;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<MarkdownSyncProgressState | null>(null);
  const [error, setError] = useState("");
  const [fresh, setFresh] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [placement, setPlacement] = useState({ right: 0, maxHeight: 600 });
  const host = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    setFresh(false);
    const position = () => {
      const bounds = trigger.current?.getBoundingClientRect();
      if (bounds)
        setPlacement({
          right: -Math.max(
            0,
            Math.min(360, window.innerWidth - 32) + 16 - bounds.right,
          ),
          maxHeight: Math.max(120, window.innerHeight - bounds.bottom - 24),
        });
    };
    position();
    window.addEventListener("resize", position);
    let active = true;
    let pending = false;
    const refresh = async () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      try {
        const next = await getMarkdownSyncProgress();
        if (active) {
          setData(next);
          setError("");
          setFresh(true);
        }
      } catch {
        if (active) {
          setError("进度读取失败，请重试");
          setFresh(false);
        }
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    document.addEventListener("visibilitychange", refresh);
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !host.current?.contains(event.target))
        setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", position);
    };
  }, [open, attempt]);
  return (
    <div
      ref={host}
      className="markdown-progress-host"
      onKeyDown={(e) => {
        if (open && e.key === "Escape") {
          e.stopPropagation();
          close();
        }
      }}
    >
      <Button
        ref={trigger}
        variant="ghost"
        aria-expanded={open}
        aria-controls="markdown-sync-progress"
        onClick={() => {
          setFresh(false);
          setOpen((value) => !value);
        }}
      >
        <Activity aria-hidden="true" size={16} strokeWidth={1.5} />
        同步进度
      </Button>
      {open && (
        <section
          id="markdown-sync-progress"
          className="markdown-progress-window"
          aria-label="同步进度"
          style={{ right: placement.right, maxHeight: placement.maxHeight }}
        >
          <header>
            <div className="heading-with-info">
              <h2>基础 MD 转换</h2>
              <InfoTooltip label="转换进度说明" align="end">
                统计本机已下载课件和已保存字幕。基础 MD 可直接阅读与检索，不消耗
                Token；AI 精炼总结单独计数。 自动任务在窗口关闭后继续；此窗口每
                5 秒更新，不启动转换或调用模型。
              </InfoTooltip>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="关闭同步进度"
              onClick={close}
            >
              <X aria-hidden="true" size={16} />
            </Button>
          </header>
          {!data && !error && <p role="status">读取进度…</p>}
          {error && (
            <div role="alert">
              {error}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setAttempt((n) => n + 1)}
              >
                重试
              </Button>
            </div>
          )}
          {data && (
            <>
              <ProgressRow name="课件" group={data.courses} />
              <ProgressRow name="字幕" group={data.transcripts} />
              {!fresh && (
                <p role="status" className="markdown-progress-current">
                  {error ? "上次读取" : "读取最新进度…"}
                  {error &&
                    `：${new Date(data.updated_at).toLocaleString("zh-CN")}`}
                </p>
              )}
              {fresh &&
                data.current.status === "running" &&
                data.current.current_file && (
                  <p
                    className="markdown-progress-current"
                    title={data.current.current_file}
                  >
                    正在{data.current.phase === "local_text" ? "转换" : "整理"}
                    ：{data.current.current_file}
                  </p>
                )}
              {fresh && data.current.status === "blocked" && (
                <p role="status">课件同步暂不可用，请检查设置</p>
              )}
              {fresh && data.current.status === "paused" && (
                <p role="status">课件同步已暂停</p>
              )}
              {!data.automatic && <p role="status">课件自动同步未开启</p>}
              {!data.ai_ready && <p role="status">AI 总结等待连接配置</p>}
              <footer>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setOpen(false);
                    onNavigate("settings");
                  }}
                >
                  设置 / Token
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setOpen(false);
                    onNavigate("knowledge");
                  }}
                >
                  查看知识库
                </Button>
              </footer>
            </>
          )}
        </section>
      )}
    </div>
  );
}
