import { useCallback, useEffect, useRef, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import {
  getMessageDetail,
  getMessages,
  markMessagesRead,
  openExternal,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type {
  MessageDetail,
  MessageFilter,
  MessageItem,
  MessageKind,
} from "@/lib/types";

const kinds: Array<{ value: MessageFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "email", label: "邮件" },
  { value: "announcement", label: "公告" },
  { value: "assignment", label: "作业" },
];

const kindLabels: Record<MessageKind, string> = {
  email: "邮件",
  announcement: "公告",
  assignment: "作业",
};

const messageKey = (item: MessageItem) => `${item.kind}:${item.source_id}`;

function messageError(reason: unknown, fallback = "未知错误") {
  return reason instanceof Error ? reason.message : fallback;
}

function isEditableTarget(target: EventTarget | null) {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        'input, textarea, select, [contenteditable="true"], [role="textbox"]',
      ),
    )
  );
}

export function MessagesView() {
  const [kind, setKind] = useState<MessageFilter>("all");
  const [items, setItems] = useState<MessageItem[] | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [detailItem, setDetailItem] = useState<MessageItem | null>(null);
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const detailRequest = useRef(0);

  const closeDetail = useCallback(() => {
    detailRequest.current += 1;
    setDetailItem(null);
    setDetail(null);
    setDetailError("");
    window.setTimeout(() => {
      if (selectedKey) rowRefs.current.get(selectedKey)?.focus();
    });
  }, [selectedKey]);

  const load = useCallback(async () => {
    setItems(null);
    setError("");
    setActionError("");
    setFeedback("");
    try {
      const data = await getMessages(kind);
      setItems(data.items);
    } catch (reason) {
      setError(messageError(reason));
    }
  }, [kind]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!items?.length) {
      setSelectedKey(null);
      return;
    }
    setSelectedKey((current) =>
      current && items.some((item) => messageKey(item) === current)
        ? current
        : messageKey(items[0]),
    );
  }, [items]);

  useEffect(() => {
    if (!detailItem) return;
    const timer = window.setTimeout(() => closeButtonRef.current?.focus());
    return () => window.clearTimeout(timer);
  }, [detailItem]);

  const openDetail = useCallback(async (item: MessageItem) => {
    const request = detailRequest.current + 1;
    detailRequest.current = request;
    setSelectedKey(messageKey(item));
    setDetailItem(item);
    setDetail(null);
    setDetailError("");
    try {
      const data = await getMessageDetail(item.kind, item.source_id);
      if (detailRequest.current === request) setDetail(data);
    } catch (reason) {
      if (detailRequest.current === request) {
        setDetailError(messageError(reason, "消息详情加载失败"));
      }
    }
  }, []);

  const markOneRead = useCallback(
    async (item: MessageItem) => {
      if (!item.is_unread || busyKey) return;
      const key = messageKey(item);
      setBusyKey(key);
      setActionError("");
      setFeedback("");
      try {
        await markMessagesRead({ kind: item.kind, ids: [item.source_id] });
        setItems(
          (current) =>
            current?.map((candidate) =>
              messageKey(candidate) === key
                ? { ...candidate, is_unread: false }
                : candidate,
            ) ?? null,
        );
        if (detailItem && messageKey(detailItem) === key) {
          setDetailItem((current) =>
            current ? { ...current, is_unread: false } : null,
          );
          setDetail((current) =>
            current ? { ...current, is_unread: false } : null,
          );
        }
        setFeedback(`已将“${item.title}”标记为已读。`);
      } catch (reason) {
        setActionError(messageError(reason, "标记已读失败"));
      } finally {
        setBusyKey(null);
      }
    },
    [busyKey, detailItem],
  );

  const markCurrentFilterRead = useCallback(async () => {
    if (busyKey) return;
    setBusyKey("all");
    setActionError("");
    setFeedback("");
    try {
      const result = await markMessagesRead({ kind, all: true });
      setItems(
        (current) =>
          current?.map((item) => ({ ...item, is_unread: false })) ?? null,
      );
      if (detailItem && (kind === "all" || detailItem.kind === kind)) {
        setDetailItem((current) =>
          current ? { ...current, is_unread: false } : null,
        );
        setDetail((current) =>
          current ? { ...current, is_unread: false } : null,
        );
      }
      setFeedback(`当前筛选已全部标为已读（更新 ${result.updated} 条）。`);
    } catch (reason) {
      setActionError(messageError(reason, "全部标记已读失败"));
    } finally {
      setBusyKey(null);
    }
  }, [busyKey, detailItem, kind]);

  const openCanvas = useCallback(async (url: string) => {
    setActionError("");
    try {
      await openExternal(url);
    } catch (reason) {
      setActionError(messageError(reason, "链接打开失败"));
    }
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && detailItem) {
        event.preventDefault();
        closeDetail();
        return;
      }
      if (
        detailItem ||
        isEditableTarget(event.target) ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        !items?.length
      ) {
        return;
      }
      const selectedIndex = Math.max(
        0,
        items.findIndex((item) => messageKey(item) === selectedKey),
      );
      if (event.key === "j" || event.key === "k") {
        event.preventDefault();
        const offset = event.key === "j" ? 1 : -1;
        const nextIndex = Math.min(
          items.length - 1,
          Math.max(0, selectedIndex + offset),
        );
        const nextKey = messageKey(items[nextIndex]);
        setSelectedKey(nextKey);
        window.setTimeout(() => rowRefs.current.get(nextKey)?.focus());
      } else if (event.key === "Enter") {
        event.preventDefault();
        void openDetail(items[selectedIndex]);
      } else if (event.key.toLowerCase() === "r" && event.shiftKey) {
        event.preventDefault();
        void markCurrentFilterRead();
      } else if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        void markOneRead(items[selectedIndex]);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [
    closeDetail,
    detailItem,
    items,
    markCurrentFilterRead,
    markOneRead,
    openDetail,
    selectedKey,
  ]);

  const unreadCount = items?.filter((item) => item.is_unread).length ?? 0;

  return (
    <div className="section-stack messages-page">
      <div className="view-intro">
        <div>
          <h2>消息收件箱</h2>
          <p>
            在应用内安全阅读纯文本详情；j/k 选择，Enter 打开，r/⇧R 标记已读。
          </p>
        </div>
        <div className="message-toolbar">
          <Tabs
            value={kind}
            onValueChange={(value) => setKind(value as MessageFilter)}
          >
            <TabsList aria-label="消息类型">
              {kinds.map((item) => (
                <TabsTrigger key={item.value} value={item.value}>
                  {item.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label="将当前筛选中的消息全部标记为已读"
            disabled={!unreadCount || busyKey !== null}
            onClick={() => void markCurrentFilterRead()}
          >
            {busyKey === "all" ? "处理中…" : "当前筛选全部已读"}
          </Button>
        </div>
      </div>

      {actionError && (
        <div className="settings-error" role="alert">
          {actionError}
        </div>
      )}
      {feedback && (
        <div className="notice" role="status" aria-live="polite">
          {feedback}
        </div>
      )}

      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : items === null ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState
          title="没有消息"
          description="这个筛选条件下暂时没有内容。"
        />
      ) : (
        <div className="list-surface" aria-label="消息列表">
          {items.map((item) => {
            const key = messageKey(item);
            const selected = selectedKey === key;
            return (
              <div
                className={`list-row message-row${selected ? " message-row-selected" : ""}${item.is_unread ? " message-row-unread" : ""}`}
                key={key}
              >
                <button
                  type="button"
                  className="message-open"
                  aria-label={`打开消息详情：${item.title}`}
                  aria-current={selected ? "true" : undefined}
                  ref={(node) => {
                    if (node) rowRefs.current.set(key, node);
                    else rowRefs.current.delete(key);
                  }}
                  onFocus={() => setSelectedKey(key)}
                  onClick={() => void openDetail(item)}
                >
                  <div className="min-w-0">
                    <div className="message-title">
                      {item.is_unread && (
                        <span className="unread-dot" aria-hidden="true" />
                      )}
                      <p className="truncate">{item.title}</p>
                      {item.is_unread && (
                        <span className="unread-label">未读</span>
                      )}
                    </div>
                    <span>
                      {item.source_label} · {kindLabels[item.kind]}
                    </span>
                  </div>
                  <span className="row-time">
                    {formatDateTime(item.occurred_at)}
                  </span>
                </button>
                {item.is_unread && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`将“${item.title}”标记为已读`}
                    disabled={busyKey !== null}
                    onClick={() => void markOneRead(item)}
                  >
                    {busyKey === key ? "处理中…" : "标为已读"}
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {detailItem && (
        <div className="message-dialog-layer">
          <button
            type="button"
            className="message-dialog-backdrop"
            aria-label="点击遮罩关闭消息详情"
            onClick={closeDetail}
          />
          <section
            className="message-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="message-detail-title"
          >
            <header className="message-dialog-header">
              <div className="min-w-0">
                <span className="status-tag">
                  {kindLabels[detailItem.kind]}
                </span>
                <h3 id="message-detail-title">{detailItem.title}</h3>
              </div>
              <Button
                ref={closeButtonRef}
                type="button"
                variant="ghost"
                size="sm"
                aria-label="关闭消息详情"
                onClick={closeDetail}
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
                  onClick={() => void openDetail(detailItem)}
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
                <div
                  className="message-detail-body"
                  role="document"
                  tabIndex={0}
                >
                  {detail.body || "（无正文）"}
                </div>
                <footer className="message-dialog-actions">
                  {detail.is_unread && (
                    <Button
                      type="button"
                      variant="outline"
                      aria-label={`将“${detail.title}”标记为已读`}
                      disabled={busyKey !== null}
                      onClick={() => void markOneRead(detailItem)}
                    >
                      标为已读
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
      )}
    </div>
  );
}
