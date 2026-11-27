import { ArrowLeft, Check, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { MessageDetailContent } from "@/components/MessageDetailContent";
import { ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { getMessageDetail, openExternal } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { MessageDetail, MessageItem } from "@/lib/types";

export function MessageReader({
  item,
  onBack,
  onRead,
  busy,
}: {
  item: MessageItem;
  onBack: () => void;
  onRead: (item: MessageItem) => void;
  busy: boolean;
}) {
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError("");
    void getMessageDetail(item.kind, item.source_id)
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch((reason) => {
        if (!cancelled)
          setError(
            reason instanceof Error ? reason.message : "消息详情加载失败",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [item.kind, item.source_id, retry]);
  return (
    <article className="message-reader" aria-label="消息详情">
      <header className="message-reader-header">
        <Button
          className="message-reader-back"
          variant="ghost"
          size="sm"
          onClick={onBack}
        >
          <ArrowLeft size={16} aria-hidden="true" /> 返回列表
        </Button>
        <h3>{item.title}</h3>
        <div className="message-reader-meta">
          <span>{item.source_label}</span>
          <time>{formatDateTime(item.occurred_at)}</time>
        </div>
        <div className="message-reader-actions">
          {item.is_unread && (
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              aria-label={`将“${item.title}”标记为已读`}
              onClick={() => onRead(item)}
            >
              <Check size={14} aria-hidden="true" /> 标为已读
            </Button>
          )}
          {detail?.url && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                void openExternal(detail.url as string).catch((reason) =>
                  setError(
                    reason instanceof Error ? reason.message : "打开失败",
                  ),
                );
              }}
            >
              <ExternalLink size={14} aria-hidden="true" /> 在 Canvas 打开
            </Button>
          )}
        </div>
      </header>
      <div
        className="message-reader-scroll"
        key={`${item.kind}:${item.source_id}`}
      >
        {error ? (
          <ErrorState
            message={error}
            retry={() => setRetry((value) => value + 1)}
          />
        ) : detail ? (
          <MessageDetailContent
            detail={detail}
            kind={item.kind}
            sourceId={item.source_id}
          />
        ) : (
          <LoadingState label="正在加载消息详情…" />
        )}
      </div>
    </article>
  );
}
