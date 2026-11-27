import { FileText, Mail, Megaphone, Paperclip, Search } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { formatDateTime } from "@/lib/format";
import type { MessageFilter, MessageItem, MessageKind } from "@/lib/types";

const messageKey = (item: MessageItem) => `${item.kind}:${item.source_id}`;
const kindIcons: Record<MessageKind, typeof Mail> = {
  email: Mail,
  announcement: Megaphone,
  assignment: FileText,
};
export function MessageInbox({
  items,
  query,
  total,
  unreadCount,
  selectedKey,
  detailItem,
  rowRefs,
  onSelect,
  onOpen,
  nextOffset,
  loadingMore,
  loadMore,
}: {
  items: MessageItem[];
  query: string;
  total: number;
  unreadCount: number;
  selectedKey: string | null;
  detailItem: MessageItem | null;
  rowRefs: Map<string, HTMLButtonElement>;
  onSelect: (key: string) => void;
  onOpen: (item: MessageItem) => void;
  nextOffset: number | null;
  loadingMore: boolean;
  loadMore: () => Promise<void>;
}) {
  return (
    <section className="message-list-pane" aria-label="收件箱">
      <div className="message-list-heading">
        <span>
          {query.trim() ? "搜索结果" : "全部消息"} <b>{total}</b>
        </span>
        <span>{unreadCount > 0 ? `${unreadCount} 未读` : ""}</span>
      </div>
      <div
        className="message-list-scroll"
        role="list"
        aria-label="消息列表"
        onScroll={(event) => {
          const target = event.currentTarget;
          if (
            target.scrollHeight - target.scrollTop - target.clientHeight <
            180
          )
            void loadMore();
        }}
      >
        {items.map((item) => {
          const key = messageKey(item);
          const Icon = kindIcons[item.kind];
          const selected =
            (selectedKey ?? (detailItem && messageKey(detailItem))) === key;
          return (
            <div
              role="listitem"
              className={`message-row${selected ? " message-row-selected" : ""}${item.is_unread ? " message-row-unread" : ""}`}
              key={key}
            >
              <button
                type="button"
                className="message-open"
                aria-label={`打开消息详情：${item.title}`}
                aria-current={selected ? "true" : undefined}
                ref={(node) => {
                  if (node) rowRefs.set(key, node);
                  else rowRefs.delete(key);
                }}
                onFocus={() => {
                  onSelect(key);
                }}
                onClick={() => onOpen(item)}
              >
                <span className={`message-kind-icon message-kind-${item.kind}`}>
                  <Icon size={17} aria-hidden="true" />
                </span>
                <div className="message-row-copy">
                  <div className="message-title">
                    {item.is_unread && (
                      <span className="unread-dot" aria-label="未读" />
                    )}
                    <p>{item.title}</p>
                  </div>
                  <div className="message-row-meta">
                    <span>{item.source_label}</span>
                    <time>{formatDateTime(item.occurred_at)}</time>
                  </div>
                  {item.preview && (
                    <p className="message-preview">{item.preview}</p>
                  )}
                  {Boolean(item.attachment_count) && (
                    <span className="message-attachment-count">
                      <Paperclip size={12} aria-hidden="true" />{" "}
                      {item.attachment_count} 个附件
                    </span>
                  )}
                </div>
              </button>
            </div>
          );
        })}
        {nextOffset !== null && (
          <Button
            className="message-load-more"
            variant="ghost"
            size="sm"
            disabled={loadingMore}
            onClick={() => void loadMore()}
          >
            {loadingMore ? "加载中…" : "加载更多"}
          </Button>
        )}
      </div>
    </section>
  );
}
export function MessageToolbar({
  info,
  kinds,
  kind,
  setKind,
  query,
  setQuery,
  searchRef,
  allTabRef,
  busyKey,
  unreadCount,
  markCurrentFilterRead,
}: {
  info: ReactNode;
  kinds: Array<{ value: MessageFilter; label: string }>;
  kind: MessageFilter;
  setKind: (kind: MessageFilter) => void;
  query: string;
  setQuery: (query: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  allTabRef: RefObject<HTMLButtonElement | null>;
  busyKey: string | null;
  unreadCount: number;
  markCurrentFilterRead: () => Promise<void>;
}) {
  return (
    <div className="message-toolbar">
      {info}
      <label className="search-control">
        <Search aria-hidden="true" />
        <input
          ref={searchRef}
          type="search"
          aria-label="搜索消息"
          placeholder="搜索标题、正文、发件人或课程"
          value={query}
          maxLength={160}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
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
  );
}

export function isEditableTarget(target: EventTarget | null) {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        'input, textarea, select, [contenteditable="true"], [role="textbox"]',
      ),
    )
  );
}

export function messageShortcutHint() {
  const platform = navigator.platform || navigator.userAgent;
  return /Mac|iPhone|iPad/i.test(platform)
    ? "J/K 选择 · ↩ 打开 · R 已读 · ⇧R 全部已读"
    : "J/K 选择 · Enter 打开 · R 已读 · Shift+R 全部已读";
}
