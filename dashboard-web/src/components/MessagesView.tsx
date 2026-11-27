import { useCallback, useEffect, useRef, useState } from "react";
import { ContentReveal } from "@/components/ContentReveal";
import {
  isEditableTarget,
  MessageInbox,
  MessageToolbar,
  messageShortcutHint,
} from "@/components/MessageInbox";
import { MessageReader } from "@/components/MessageReader";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
import {
  getMessageDetail,
  getMessageReference,
  getMessages,
  markMessagesRead,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { parseMessageLink } from "@/lib/messageLink";
import type { MessageFilter, MessageItem } from "@/lib/types";

const kinds: Array<{ value: MessageFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "email", label: "邮件" },
  { value: "announcement", label: "公告" },
  { value: "assignment", label: "作业" },
];

const messageKey = (item: MessageItem) => `${item.kind}:${item.source_id}`;

function messageError(reason: unknown, fallback = "未知错误") {
  return reason instanceof Error ? reason.message : fallback;
}

export function MessagesView() {
  const [kind, setKind] = useState<MessageFilter>("all");
  const [items, setItems] = useState<MessageItem[] | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [detailItem, setDetailItem] = useState<MessageItem | null>(null);
  const [query, setQuery] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [total, setTotal] = useState(0);
  const [totalUnread, setTotalUnread] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setSearchQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const allTabRef = useRef<HTMLButtonElement | null>(null);
  const focusTimerRef = useRef<number | null>(null);
  const { showToast } = useToast();

  const closeDetail = useCallback(() => {
    setDetailItem(null);
  }, []);

  const loadRequestRef = useRef(0);
  const load = useCallback(async () => {
    const requestId = ++loadRequestRef.current;
    setItems(null);
    setError("");
    try {
      const data = await getMessages(kind, { query: searchQuery, limit: 60 });
      if (requestId === loadRequestRef.current) {
        setItems(data.items);
        setTotal(data.total ?? data.items.length);
        setTotalUnread(
          data.unread ?? data.items.filter((item) => item.is_unread).length,
        );
        setNextOffset(data.next_offset ?? null);
        setDetailItem(null);
      }
    } catch (reason) {
      if (requestId === loadRequestRef.current) setError(messageError(reason));
    }
  }, [kind, searchQuery]);

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

  const openDetail = useCallback((item: MessageItem) => {
    const key = messageKey(item);
    setSelectedKey(key);
    setDetailItem(item);
  }, []);

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
    setTotalUnread((count) => Math.max(0, count - 1));
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
      const result = await markMessagesRead({
        kind,
        all: true,
        query: searchQuery,
      });
      // Only the list loaded for this filter may be cleared; a tab switch mid-request loads another filter.
      if (listRequest === loadRequestRef.current) {
        setTotalUnread(0);
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
  }, [
    busyKey,
    detailItem,
    kind,
    searchQuery,
    scheduleRowFocus,
    selectedKey,
    showToast,
  ]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !document.querySelector("[data-modal-layer]")
      )
        closeDetail();
      if (
        event.defaultPrevented ||
        event.isComposing ||
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
    closeDetail,
    detailItem,
    items,
    markCurrentFilterRead,
    markOneRead,
    openDetail,
    scheduleRowFocus,
    selectedKey,
  ]);

  useEffect(() => {
    if (items === null) return;
    let cancelled = false;
    const openCitation = () => {
      const href = window.sessionStorage.getItem("sjtu-message-citation");
      const route = href && parseMessageLink(href);
      if (!route) return;
      window.sessionStorage.removeItem("sjtu-message-citation");
      const request = route.ref
        ? getMessageReference(route.kind, route.ref).then((value) => value.item)
        : getMessageDetail(route.kind, route.sourceId as string).then(
            (detail) => ({
              ...detail,
              kind: route.kind,
              source_id: route.sourceId as string,
            }),
          );
      void request
        .then((item) => {
          if (!cancelled) setDetailItem(item);
        })
        .catch((reason) => {
          if (!cancelled)
            showToast({
              kind: "error",
              message: messageError(reason, "引用消息不存在"),
            });
        });
    };
    openCitation();
    window.addEventListener("sjtu-message-citation-ready", openCitation);
    return () => {
      cancelled = true;
      window.removeEventListener("sjtu-message-citation-ready", openCitation);
    };
  }, [items, showToast]);

  const unreadCount = totalUnread;
  const selectedItem =
    detailItem ??
    items?.find((item) => messageKey(item) === selectedKey) ??
    items?.[0];
  const loadMore = async () => {
    if (nextOffset === null || loadingMore) return;
    const request = loadRequestRef.current;
    setLoadingMore(true);
    try {
      const data = await getMessages(kind, {
        query: searchQuery,
        offset: nextOffset,
        limit: 60,
      });
      if (request === loadRequestRef.current) {
        setItems((current) => [
          ...(current ?? []),
          ...data.items.filter(
            (item) =>
              !current?.some((old) => messageKey(old) === messageKey(item)),
          ),
        ]);
        setNextOffset(data.next_offset ?? null);
      }
    } catch (reason) {
      showToast({
        kind: "error",
        message: messageError(reason, "加载更多消息失败"),
      });
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <ContentReveal
      className="section-stack messages-page"
      ready={items !== null || Boolean(error)}
    >
      <h2 className="sr-only">消息中心</h2>
      <MessageToolbar
        info={
          <InfoTooltip label="消息快捷键">{messageShortcutHint()}</InfoTooltip>
        }
        kinds={kinds}
        kind={kind}
        setKind={setKind}
        query={query}
        setQuery={setQuery}
        searchRef={searchRef}
        allTabRef={allTabRef}
        busyKey={busyKey}
        unreadCount={unreadCount}
        markCurrentFilterRead={markCurrentFilterRead}
      />

      <p className="sr-only" aria-live="polite">
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
          title={
            searchQuery
              ? "没有找到相关消息"
              : kind === "all"
                ? "暂无消息"
                : "当前筛选没有结果"
          }
          description={
            kind === "all"
              ? "完成同步后，邮件、公告与作业消息会显示在这里。"
              : "可以清除筛选查看全部消息。"
          }
          action={
            kind !== "all" || searchQuery ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setQuery("");
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
          className={`message-workspace${detailItem ? " message-workspace-reading" : ""}`}
        >
          <MessageInbox
            items={items}
            query={query}
            total={total}
            unreadCount={unreadCount}
            selectedKey={selectedKey}
            detailItem={detailItem}
            rowRefs={rowRefs.current}
            onSelect={(key) => {
              setSelectedKey(key);
              setDetailItem(null);
            }}
            onOpen={openDetail}
            nextOffset={nextOffset}
            loadingMore={loadingMore}
            loadMore={loadMore}
          />
          {selectedItem && (
            <MessageReader
              key={messageKey(selectedItem)}
              item={selectedItem}
              onBack={closeDetail}
              onRead={(item) => void markOneRead(item)}
              busy={busyKey !== null}
            />
          )}
        </div>
      )}
    </ContentReveal>
  );
}
