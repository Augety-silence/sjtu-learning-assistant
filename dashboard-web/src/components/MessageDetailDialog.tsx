import { useCallback, useEffect, useRef, useState } from "react";
import { MessageDetailContent } from "@/components/MessageDetailContent";
import { LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import { getMessageDetail, markMessagesRead, openExternal } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { MessageDetail, MessageItem, MessageKind } from "@/lib/types";

const kindLabels: Record<MessageKind, string> = {
  email: "邮件",
  announcement: "公告",
  assignment: "作业",
};

function messageError(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

export function MessageDetailDialog({
  item,
  onClose,
  onMarkedRead,
}: {
  item: MessageItem;
  onClose: () => void;
  onMarkedRead?: (item: MessageItem) => void;
}) {
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [requestVersion, setRequestVersion] = useState(0);
  const [markingRead, setMarkingRead] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const { showToast } = useToast();

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setDetailError("");
    void getMessageDetail(item.kind, item.source_id)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((reason) => {
        if (!cancelled) {
          setDetailError(messageError(reason, "消息详情加载失败"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [item.kind, item.source_id, requestVersion]);

  useEffect(() => {
    const timer = window.setTimeout(() => closeButtonRef.current?.focus());
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const markRead = useCallback(async () => {
    if (markingRead) return;
    const toastId = `message-read:${item.kind}:${item.source_id}`;
    setMarkingRead(true);
    showToast({
      id: toastId,
      kind: "info",
      message: `正在将“${item.title}”标记为已读…`,
      duration: 0,
    });
    try {
      await markMessagesRead({ kind: item.kind, ids: [item.source_id] });
      setDetail((current) =>
        current ? { ...current, is_unread: false } : current,
      );
      onMarkedRead?.(item);
      showToast({
        id: toastId,
        kind: "success",
        message: `已将“${item.title}”标记为已读。`,
      });
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: messageError(reason, "标记已读失败"),
      });
    } finally {
      setMarkingRead(false);
    }
  }, [item, markingRead, onMarkedRead, showToast]);

  const openCanvas = useCallback(
    async (url: string) => {
      try {
        await openExternal(url);
        showToast({ kind: "success", message: "已在 Canvas 打开消息。" });
      } catch (reason) {
        showToast({
          kind: "error",
          message: messageError(reason, "链接打开失败"),
        });
      }
    },
    [showToast],
  );

  return (
    <div className="message-dialog-layer">
      <button
        type="button"
        className="message-dialog-backdrop"
        aria-label="点击遮罩关闭消息详情"
        onClick={onClose}
      />
      <section
        className="message-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="message-detail-title"
      >
        <header className="message-dialog-header">
          <div className="min-w-0">
            <span className="status-tag">{kindLabels[item.kind]}</span>
            <h3 id="message-detail-title">{item.title}</h3>
          </div>
          <Button
            ref={closeButtonRef}
            type="button"
            variant="ghost"
            size="sm"
            aria-label="关闭消息详情"
            onClick={onClose}
          >
            关闭
          </Button>
        </header>
        {detailError ? (
          <div className="message-detail-state" role="alert">
            <p>{detailError}</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label="重试加载消息详情"
              onClick={() => setRequestVersion((value) => value + 1)}
            >
              重试
            </Button>
          </div>
        ) : detail === null ? (
          <LoadingState label="正在加载消息详情…" />
        ) : (
          <>
            <div className="message-detail-meta">
              <span>{detail.source_label}</span>
              <span>{formatDateTime(detail.occurred_at)}</span>
            </div>
            <MessageDetailContent
              detail={detail}
              kind={item.kind}
              sourceId={item.source_id}
            />
            <footer className="message-dialog-actions">
              {detail.is_unread && (
                <Button
                  type="button"
                  variant="outline"
                  aria-label={`将“${detail.title}”标记为已读`}
                  disabled={markingRead}
                  onClick={() => void markRead()}
                >
                  {markingRead ? "处理中…" : "标为已读"}
                </Button>
              )}
              {detail.url && (
                <Button
                  type="button"
                  aria-label={`在 Canvas 打开“${detail.title}”`}
                  onClick={() => void openCanvas(detail.url as string)}
                >
                  在 Canvas 打开
                </Button>
              )}
            </footer>
          </>
        )}
      </section>
    </div>
  );
}
