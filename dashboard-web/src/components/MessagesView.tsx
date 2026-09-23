import { useCallback, useEffect, useRef, useState } from "react";
import { MessageDetailDialog } from "@/components/MessageDetailDialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { getMessages, markMessagesRead } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { MessageFilter, MessageItem, MessageKind } from "@/lib/types";

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
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [detailItem, setDetailItem] = useState<MessageItem | null>(null);
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const focusTimerRef = useRef<number | null>(null);
  const { showToast } = useToast();

  const closeDetail = useCallback(() => {
    setDetailItem(null);
  }, []);

  const load = useCallback(async () => {
    setItems(null);
    setError("");
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

  const openDetail = useCallback(
    (item: MessageItem, trigger?: HTMLElement | null) => {
      const key = messageKey(item);
      setSelectedKey(key);
      detailTriggerRef.current = trigger ?? rowRefs.current.get(key) ?? null;
      setDetailItem(item);
    },
    [],
  );

  const scheduleRowFocus = useCallback((key: string) => {
    if (focusTimerRef.current !== null) {
      window.clearTimeout(focusTimerRef.current);
    }
    focusTimerRef.current = window.setTimeout(() => {
      focusTimerRef.current = null;
      rowRefs.current.get(key)?.focus();
    }, 0);
  }, []);

  useEffect(
    () => () => {
      if (focusTimerRef.current !== null) {
        window.clearTimeout(focusTimerRef.current);
      }
    },
    [],
  );

  const applyRead = useCallback((item: MessageItem) => {
    const key = messageKey(item);
    setItems(
      (current) =>
        current?.map((candidate) =>
          messageKey(candidate) === key
            ? { ...candidate, is_unread: false }
            : candidate,
        ) ?? null,
    );
    setDetailItem((current) =>
      current && messageKey(current) === key
        ? { ...current, is_unread: false }
        : current,
    );
  }, []);

  const markOneRead = useCallback(
    async (item: MessageItem) => {
      if (!item.is_unread || busyKey) return;
      const key = messageKey(item);
      const toastId = `message-read:${key}`;
      setBusyKey(key);
      showToast({
        id: toastId,
        kind: "info",
        message: `正在将“${item.title}”标记为已读…`,
        duration: 0,
      });
      try {
        await markMessagesRead({ kind: item.kind, ids: [item.source_id] });
        applyRead(item);
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
        setBusyKey(null);
      }
    },
    [applyRead, busyKey, showToast],
  );

  const markCurrentFilterRead = useCallback(async () => {
    if (busyKey) return;
    const toastId = `messages-read-all:${kind}`;
    setBusyKey("all");
    showToast({
      id: toastId,
      kind: "info",
      message: "正在将当前筛选标记为已读…",
      duration: 0,
    });
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
      }
      showToast({
        id: toastId,
        kind: "success",
        message: `当前筛选已全部标为已读（更新 ${result.updated} 条）。`,
      });
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: messageError(reason, "全部标记已读失败"),
      });
    } finally {
      setBusyKey(null);
    }
  }, [busyKey, detailItem, kind, showToast]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
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
        scheduleRowFocus(nextKey);
      } else if (event.key === "Enter") {
        event.preventDefault();
        openDetail(items[selectedIndex]);
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
    detailItem,
    items,
    markCurrentFilterRead,
    markOneRead,
    openDetail,
    scheduleRowFocus,
    selectedKey,
  ]);

  const unreadCount = items?.filter((item) => item.is_unread).length ?? 0;

  return (
    <div className="section-stack messages-page">
      <div className="view-intro">
        <div>
          <h2>消息收件箱</h2>
          <p>在应用内安全阅读消息详情；j/k 选择，Enter 打开，r/⇧R 标记已读。</p>
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

      <p className="result-count" aria-live="polite">
        {items
          ? `共 ${items.length} 条消息${unreadCount ? `，${unreadCount} 条未读` : ""}`
          : ""}
      </p>
      {error ? (
        <ErrorState message={error} retry={load} />
      ) : items === null ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState
          title={kind === "all" ? "暂无消息" : "当前筛选没有结果"}
          description={
            kind === "all"
              ? "完成同步后，邮件、公告与作业消息会显示在这里。"
              : "可以清除筛选查看全部消息。"
          }
          action={
            kind !== "all" ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setKind("all")}
              >
                清除筛选
              </Button>
            ) : (
              <Button variant="outline" size="sm" onClick={() => void load()}>
                重新检查
              </Button>
            )
          }
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
                  onClick={(event) => openDetail(item, event.currentTarget)}
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
        <MessageDetailDialog
          item={detailItem}
          onClose={closeDetail}
          onMarkedRead={applyRead}
          triggerRef={detailTriggerRef}
        />
      )}
    </div>
  );
}
