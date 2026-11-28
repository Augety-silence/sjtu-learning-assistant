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
      {group.inventory && (
        <details>
          <summary>原件统计 · 未登记 {group.inventory.unregistered}</summary>
          <p>
            本地原件 {group.inventory.local_originals} · 可自动编译{" "}
            {group.inventory.supported} · 其他格式{" "}
            {group.inventory.unsupported_local}
          </p>
          <p>
            已登记 {group.inventory.registered} · 未登记{" "}
            {group.inventory.unregistered} · 未纳入下载记录的可编译原件{" "}
            {group.inventory.untracked_supported}
          </p>
          {group.inventory.database_total !== undefined && (
            <p>
              课程资料记录 {group.inventory.database_total} · 未下载{" "}
              {group.inventory.not_downloaded}
              {(group.inventory.markdown_only ?? 0) > 0 &&
                ` · 仅保留 MD ${group.inventory.markdown_only}`}
            </p>
          )}
          <p>统计包含未登记原件；自动编译仍按现有下载记录和设置执行。</p>
        </details>
      )}
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
  embedded = false,
}: {
  onNavigate: (view: "knowledge" | "settings") => void;
  embedded?: boolean;
}) {
  const [open, setOpen] = useState(embedded);
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
      if (
        !embedded &&
        event.target instanceof Node &&
        !host.current?.contains(event.target)
      )
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
  }, [open, attempt, embedded]);
  return (
    <div
      ref={host}
      className={`markdown-progress-host ${embedded ? "is-embedded" : ""}`}
      onKeyDown={(e) => {
        if (open && e.key === "Escape") {
          e.stopPropagation();
          close();
        }
      }}
    >
      {!embedded && (
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
      )}
      {open && (
        <section
          id="markdown-sync-progress"
          className="markdown-progress-window"
          aria-label="同步进度"
          style={
            embedded
              ? undefined
              : { right: placement.right, maxHeight: placement.maxHeight }
          }
        >
          <header>
            <div className="heading-with-info">
              <h2>基础 MD 转换</h2>
              <InfoTooltip label="转换进度说明" align="end">
                统计本机可自动编译的课件原件和已保存字幕，未登记原件也计入待转换。基础
                MD 可直接阅读与检索，不消耗 Token；AI 精炼总结单独计数。
                自动任务在窗口关闭后继续；此窗口每 5
                秒更新，不启动转换或调用模型。
              </InfoTooltip>
            </div>
            {!embedded && (
              <Button
                variant="ghost"
                size="icon"
                aria-label="关闭同步进度"
                onClick={close}
              >
                <X aria-hidden="true" size={16} />
              </Button>
            )}
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
              {data.current_term_md && (
                <section
                  className="markdown-progress-row"
                  aria-label="本学期后台补齐"
                >
                  <div className="markdown-progress-heading">
                    <h3>本学期后台补齐</h3>
                    <span>
                      {data.current_term_md.enabled
                        ? "后台扫描已开启"
                        : "已暂停"}
                    </span>
                  </div>
                  <p>
                    本学期资料 {data.current_term_md.total} · 原有下载{" "}
                    {data.current_term_md.existing}
                  </p>
                  <dl>
                    <div>
                      <dt>补齐 MD</dt>
                      <dd>{data.current_term_md.completed}</dd>
                    </div>
                    <div>
                      <dt>待处理</dt>
                      <dd>{data.current_term_md.pending}</dd>
                    </div>
                    <div>
                      <dt>异常</dt>
                      <dd>{data.current_term_md.failed}</dd>
                    </div>
                  </dl>
                  <p>临时下载转为可读 MD 并校验后清理原件，原有下载保留。</p>
                  {data.current_term_md.current_file && (
                    <p>正在处理：{data.current_term_md.current_file}</p>
                  )}
                  {data.current_term_md.status === "blocked" && (
                    <p role="status">后台补齐暂不可用，等待下次扫描</p>
                  )}
                </section>
              )}
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
