import { useCallback, useEffect, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { invoke, openExternal } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { MessageItem } from "@/lib/types";

const kinds = [
  { value: "all", label: "全部" },
  { value: "email", label: "邮件" },
  { value: "announcement", label: "公告" },
];

export function MessagesView() {
  const [kind, setKind] = useState("all");
  const [items, setItems] = useState<MessageItem[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setItems(null);
    setError("");
    try {
      const data = await invoke<{ items: MessageItem[] }>("messages", { kind });
      setItems(data.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "未知错误");
    }
  }, [kind]);
  useEffect(() => {
    void load();
  }, [load]);
  const open = (url: string | null) => {
    if (url)
      void openExternal(url).catch((reason) =>
        setError(reason instanceof Error ? reason.message : "链接打开失败"),
      );
  };

  return (
    <div className="section-stack">
      <div className="view-intro">
        <div>
          <h2>消息收件箱</h2>
          <p>聚合课程公告与邮件标题，不展示邮件全文。</p>
        </div>
        <Tabs value={kind} onValueChange={setKind}>
          <TabsList>
            {kinds.map((item) => (
              <TabsTrigger key={item.value} value={item.value}>
                {item.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
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
        <div className="list-surface">
          {items.map((item) => (
            <button
              type="button"
              className="list-row list-row-button"
              key={`${item.kind}-${item.title}-${item.occurred_at}`}
              disabled={!item.url}
              onClick={() => open(item.url)}
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
          ))}
        </div>
      )}
    </div>
  );
}
