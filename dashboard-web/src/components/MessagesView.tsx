import { Search } from "lucide-react";
import { AnimatePresence } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ContentReveal } from "@/components/ContentReveal";
import { MessageDetailDialog } from "@/components/MessageDetailDialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
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

function messageShortcutHint() {
  const platform = navigator.platform || navigator.userAgent;
  return /Mac|iPhone|iPad/i.test(platform)
    ? "J/K 选择 · ↩ 打开 · R 已读 · ⇧R 全部已读"
    : "J/K 选择 · Enter 打开 · R 已读 · Shift+R 全部已读";
}

export function MessagesView() {
  const [kind, setKind] = useState<MessageFilter>("all");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<MessageItem[] | null>(null);
  const [totalCount, setTotalCount] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [compactReading, setCompactReading] = useState(false);
  const [error, setError] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [detailItem, setDetailItem] = useState<MessageItem | null>(null);
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const allTabRef = useRef<HTMLButtonElement | null>(null);
  const focusTimerRef = useRef<number | null>(null);
  const { showToast } = useToast();

  const closeDetail = useCallback(() => {
    setDetailItem(null);
    setCompactReading(false);
  }, []);

  const loadRequestRef = useRef(0);
  const load = useCallback(async () => {
    const requestId = ++loadRequestRef.current;
    setItems(null);
    setTotalCount(0);
    setHasMore(false);
    setError("");
    try {
      const data = await getMessages(kind, { query, offset: 0, limit: 60 });
      if (requestId === loadRequestRef.current) {
        setItems(data.items);
        setTotalCount(data.total_count);
        setHasMore(data.has_more);
      }
    } catch (reason) {
      if (requestId === loadRequestRef.current) setError(messageError(reason));
    }
  }, [kind, query]);

  const loadMore = useCallback(async () => {
    if (!hasMore || loadingMore || !items) return;
    const requestId = loadRequestRef.current;
    setLoadingMore(true);
    try {
      const data = await getMessages(kind, {
        query,
        offset: items.length,
        limit: 60,
      });
      if (requestId === loadRequestRef.current) {
        setItems((current) => {
          const existing = new Set((current ?? []).map(messageKey));
          return [
            ...(current ?? []),
            ...data.items.filter((item) => !existing.has(messageKey(item))),
          ];
        });
        setTotalCount(data.total_count);
        setHasMore(data.has_more);
      }
    } catch (reason) {
      if (requestId === loadRequestRef.current) setError(messageError(reason));
    } finally {
      if (requestId === loadRequestRef.current) setLoadingMore(false);
    }
  }, [hasMore, items, kind, loadingMore, query]);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [queryInput]);

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
    if (
      items &&
      detailItem &&
      !items.some(
        (candidate) => messageKey(candidate) === messageKey(detailItem),
      )
    ) {
      setDetailItem(null);
      setCompactReading(false);
    }
  }, [detailItem, items]);

  const openDetail = useCallback(
    (item: MessageItem, trigger?: HTMLElement | null) => {
      const key = messageKey(item);
      setSelectedKey(key);
      detailTriggerRef.current = trigger ?? rowRefs.current.get(key) ?? null;
      setDetailItem(item);
      setCompactReading(
        typeof window.matchMedia !== "function" ||
          window.matchMedia("(max-width: 900px)").matches,
      );
    },
    [],
  );

  const scheduleRowFocus = useCallback(
    (key: string, onlyIfFocusLost = false) => {
      if (focusTimerRef.current !== null) {
        window.clearTimeout(focusTimerRef.current);
      }
      focusTimerRef.current = window.setTimeout(() => {
        focusTimerRef.current = null;
        const active = document.activeElement;
        // Busy buttons are disabled (then unmounted); Chromium/WebView2 drops focus to <body>.
        if (
          onlyIfFocusLost &&
          active instanceof HTMLElement &&
          active !== document.body &&
          active.isConnected &&
          !active.matches(":disabled")
        ) {
          return;
        }
        rowRefs.current.get(key)?.focus();
      }, 0);
    },
    [],
  );

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
        // The clicked "标为已读" button disables and unmounts; return focus to its row.
        scheduleRowFocus(key, true);
      }
    },
    [applyRead, busyKey, scheduleRowFocus, showToast],
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
    const listRequest = loadRequestRef.current;
    try {
      const result = await markMessagesRead({ kind, all: true });
      // Only the list loaded for this filter may be cleared; a tab switch mid-request loads another filter.
      if (listRequest === loadRequestRef.current) {
        setItems(
          (current) =>
            current?.map((item) => ({ ...item, is_unread: false })) ?? null,
        );
      }
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
      // "全部已读" becomes disabled once nothing is unread; keep keyboard focus in the list.
      if (selectedKey) scheduleRowFocus(selectedKey, true);
    }
  }, [busyKey, detailItem, kind, scheduleRowFocus, selectedKey, showToast]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        detailItem ||
        document.querySelector(
          '[data-modal-layer]:not([aria-hidden="true"])',
        ) ||
        isEditableTarget(event.target) ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        !items?.length
      ) {
        return;
      }
      const activeElement = document.activeElement;
      if (
        !(activeElement instanceof Element) ||
        !activeElement.closest(".messages-page")
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
        if (
          event.target instanceof Element &&
          event.target.closest(
            'button, a[href], summary, [role="button"], [role="tab"], [role="menuitem"]',
          )
        ) {
          return;
        }
        event.preventDefault();
        openDetail(items[selectedIndex]);
      } else if (event.key.toLowerCase() === "r" && event.shiftKey) {
        event.preventDefault();
        // Mirror the disabled "全部已读" button: never mark only unloaded (invisible) messages.
        if (items.some((item) => item.is_unread)) void markCurrentFilterRead();
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
    <ContentReveal
      className="section-stack messages-page"
      ready={items !== null || Boolean(error)}
    >
      <div className="view-intro">
        <div className="heading-with-info">
          <h2>消息收件箱</h2>
          <InfoTooltip label="消息快捷键">{messageShortcutHint()}</InfoTooltip>
        </div>
        <div className="message-search-row">
          <label className="search-control" role="search">
            <Search aria-hidden="true" />
            <input
              value={queryInput}
              onChange={(event) => setQueryInput(event.target.value)}
              placeholder="搜索标题、正文、发件人或课程"
              aria-label="搜索消息"
            />
          </label>
          <span className="message-count" aria-live="polite">
            共 {totalCount} 条{unreadCount ? ` · ${unreadCount} 条未读` : ""}
          </span>
        </div>
        <div className="message-toolbar">
          <Tabs
            value={kind}
            onValueChange={(value) => setKind(value as MessageFilter)}
          >
            <TabsList indicator aria-label="消息类型">
              {kinds.map((item) => (
                <TabsTrigger
                  key={item.value}
                  value={item.value}
                  ref={item.value === "all" ? allTabRef : undefined}
                >
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

      <p className="sr-only" aria-live="polite">
        {items
          ? `已加载 ${items.length} 条，共 ${totalCount} 条消息${unreadCount ? `，当前页 ${unreadCount} 条未读` : ""}`
          : ""}
      </p>
      {error ? (
        <ErrorState message={error} retry={load} />
      ) : items === null ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState
          title={
            query
              ? "没有找到匹配消息"
              : kind === "all"
                ? "暂无消息"
                : "当前筛选没有结果"
          }
          description={
            query
              ? "试试缩短关键词，或切换消息类型。"
              : kind === "all"
                ? "完成同步后，邮件、公告与作业消息会显示在这里。"
                : "可以清除筛选查看全部消息。"
          }
          action={
            kind !== "all" ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setKind("all");
                  window.setTimeout(() => allTabRef.current?.focus(), 0);
                }}
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
        <div
          className={`message-layout${compactReading ? " message-layout-reading" : ""}`}
        >
          <div className="message-list-column">
            <div className="list-surface" role="list" aria-label="消息列表">
              {items.map((item) => {
                const key = messageKey(item);
                const selected = selectedKey === key;
                return (
                  <div
                    role="listitem"
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
            {hasMore ? (
              <div className="message-load-more">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                >
                  {loadingMore
                    ? "正在加载…"
                    : `继续加载（已显示 ${items.length} / ${totalCount}）`}
                </Button>
              </div>
            ) : null}
          </div>
          <section className="message-detail-column" aria-label="消息正文">
            <AnimatePresence initial={false}>
              {detailItem ? (
                <MessageDetailDialog
                  item={detailItem}
                  presentation={compactReading ? "dialog" : "pane"}
                  onClose={closeDetail}
                  onMarkedRead={applyRead}
                  triggerRef={detailTriggerRef}
                />
              ) : (
                <div className="message-detail-empty">
                  选择一条消息查看正文、来源和附件。
                </div>
              )}
            </AnimatePresence>
          </section>
        </div>
      )}
    </ContentReveal>
  );
}
