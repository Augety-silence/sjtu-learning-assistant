import { FileText, Mail, Megaphone, Paperclip } from "lucide-react";
import { formatDateTime } from "@/lib/format";
import type { MessageItem, MessageKind } from "@/lib/types";
import { V2Button } from "./primitives";

const icons: Record<MessageKind, typeof Mail> = {
  email: Mail,
  announcement: Megaphone,
  assignment: FileText,
};
export function V2MessageList({
  items,
  total,
  unreadCount,
  selectedKey,
  rowRefs,
  onSelect,
  onOpen,
  nextOffset,
  loadingMore,
  loadMore,
}: {
  items: MessageItem[];
  total: number;
  unreadCount: number;
  selectedKey: string | null;
  rowRefs: Map<string, HTMLButtonElement>;
  onSelect: (key: string) => void;
  onOpen: (item: MessageItem) => void;
  nextOffset: number | null;
  loadingMore: boolean;
  loadMore: () => Promise<void>;
}) {
  return (
    <section className="v2-message-inbox" aria-label="收件箱">
      <header>
        <strong>
          消息列表 <small>{total}</small>
        </strong>
        <small>{unreadCount ? `${unreadCount} 条未读` : ""}</small>
      </header>
      <div
        className="v2-message-list"
        role="list"
        aria-label="消息列表"
        onScroll={(e) => {
          const el = e.currentTarget;
          if (el.scrollHeight - el.scrollTop - el.clientHeight < 180)
            void loadMore();
        }}
      >
        {items.map((item) => {
          const key = `${item.kind}:${item.source_id}`,
            Icon = icons[item.kind];
          return (
            <div role="listitem" key={key}>
              <button
                type="button"
                ref={(el) => {
                  if (el) rowRefs.set(key, el);
                  else rowRefs.delete(key);
                }}
                aria-label={`打开消息详情：${item.title}`}
                aria-current={selectedKey === key ? "true" : undefined}
                className="v2-message-row"
                onFocus={() => onSelect(key)}
                onClick={() => onOpen(item)}
              >
                <div className="v2-message-row-meta">
                  <span>
                    <Icon />
                    {item.kind === "email"
                      ? "邮件"
                      : item.kind === "announcement"
                        ? "Canvas 公告"
                        : "Canvas 作业"}
                  </span>
                  <time>{formatDateTime(item.occurred_at)}</time>
                </div>
                <h3>
                  {item.is_unread && <i aria-label="未读" />}
                  {item.title}
                </h3>
                <p>{item.source_label}</p>
                {item.preview && <small>{item.preview}</small>}
                {Boolean(item.attachment_count) && (
                  <span className="v2-message-attachment">
                    <Paperclip />
                    {item.attachment_count} 个附件
                  </span>
                )}
              </button>
            </div>
          );
        })}
        {nextOffset !== null && (
          <V2Button disabled={loadingMore} onClick={() => void loadMore()}>
            {loadingMore ? "加载中…" : "加载更多"}
          </V2Button>
        )}
      </div>
    </section>
  );
}
