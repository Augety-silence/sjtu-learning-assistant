import { useCallback, useEffect, useState } from "react";
import { MessageDetailDialog } from "@/components/MessageDetailDialog";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Section,
} from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import { invoke, openExternal } from "@/lib/api";
import { deadlineDistance, formatDateTime } from "@/lib/format";
import type { MessageItem, OverviewData, ViewName } from "@/lib/types";

const messageKey = (item: MessageItem) => `${item.kind}:${item.source_id}`;

export function OverviewView({
  navigate,
}: {
  navigate: (view: ViewName) => void;
}) {
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState("");
  const [detailItem, setDetailItem] = useState<MessageItem | null>(null);
  const { showToast } = useToast();
  const load = useCallback(async () => {
    setError("");
    try {
      setData(await invoke<OverviewData>("overview"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "未知错误");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const open = async (url: string | null) => {
    if (!url) return;
    try {
      await openExternal(url);
      showToast({ kind: "success", message: "已在浏览器打开截止事项。" });
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "链接打开失败",
      });
    }
  };

  const markMessageRead = (item: MessageItem) => {
    setData((current) => {
      if (!current) return current;
      const wasUnread = current.messages.some(
        (candidate) =>
          messageKey(candidate) === messageKey(item) && candidate.is_unread,
      );
      return {
        ...current,
        unread_emails:
          item.kind === "email" && wasUnread
            ? Math.max(0, current.unread_emails - 1)
            : current.unread_emails,
        messages: current.messages.map((candidate) =>
          messageKey(candidate) === messageKey(item)
            ? { ...candidate, is_unread: false }
            : candidate,
        ),
      };
    });
    setDetailItem((current) =>
      current ? { ...current, is_unread: false } : current,
    );
  };

  if (error) return <ErrorState message={error} retry={() => void load()} />;
  if (!data) return <LoadingState label="正在汇总学习信息…" />;

  return (
    <div className="section-stack">
      <section className="kpi-grid" aria-label="学习概览">
        <div className="kpi">
          <p>课程数</p>
          <strong>{data.courses}</strong>
          <span>已同步课程</span>
        </div>
        <div className="kpi">
          <p>未来 7 天作业</p>
          <strong>{data.upcoming_deadlines}</strong>
          <span>尚未完成</span>
        </div>
        <div className="kpi">
          <p>未读邮件</p>
          <strong>{data.unread_emails}</strong>
          <span>等待处理</span>
        </div>
      </section>
      <Section
        title="临期事项"
        action={
          <Button variant="link" onClick={() => navigate("deadlines")}>
            查看全部
          </Button>
        }
      >
        <div className="list-surface">
          {data.deadlines.length === 0 ? (
            <EmptyState
              title="近期没有截止事项"
              description="未来 7 天内暂无未完成作业。"
            />
          ) : (
            data.deadlines.map((item) => (
              <button
                type="button"
                className="list-row list-row-button"
                key={item.source_id}
                disabled={!item.url}
                onClick={() => void open(item.url)}
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{item.title}</p>
                  <span>{item.course}</span>
                </div>
                <div className="row-meta">
                  <span className="status-warning">
                    {deadlineDistance(item.due_at)}
                  </span>
                  <span>{formatDateTime(item.due_at)}</span>
                </div>
              </button>
            ))
          )}
        </div>
      </Section>
      <Section
        title="最新消息"
        action={
          <Button variant="link" onClick={() => navigate("messages")}>
            查看全部
          </Button>
        }
      >
        <div className="list-surface">
          {data.messages.length === 0 ? (
            <EmptyState
              title="暂无最新消息"
              description="同步后，邮件与课程公告会显示在这里。"
            />
          ) : (
            data.messages.map((item) => (
              <button
                type="button"
                className="list-row list-row-button"
                key={messageKey(item)}
                aria-label={`打开消息详情：${item.title}`}
                onClick={() => setDetailItem(item)}
              >
                <div className="min-w-0">
                  <div className="message-title">
                    {item.is_unread && <span className="unread-dot" />}
                    <p className="truncate font-medium">{item.title}</p>
                  </div>
                  <span>
                    {item.source_label} ·{" "}
                    {item.kind === "email" ? "邮件" : "公告"}
                  </span>
                </div>
                <span className="row-time">
                  {formatDateTime(item.occurred_at)}
                </span>
              </button>
            ))
          )}
        </div>
      </Section>
      {detailItem && (
        <MessageDetailDialog
          item={detailItem}
          onClose={() => setDetailItem(null)}
          onMarkedRead={markMessageRead}
        />
      )}
    </div>
  );
}
