import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import {
  completeKnowledgeText,
  copyKnowledgeText,
} from "@/lib/compiledKnowledgeCopy";

export function CopyKnowledgeButton({
  id,
  version = "",
}: {
  id: string;
  version?: string;
}) {
  const identity = id + ":" + version;
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [ready, setReady] = useState<{ id: string; text: string } | null>(null);
  const [raw, setRaw] = useState(false);
  const generation = useRef(0);
  const latestId = useRef(identity);
  latestId.current = identity;
  useEffect(() => {
    ++generation.current;
    setBusy(false);
    setMessage("");
    setReady(null);
    setRaw(false);
    return () => {
      ++generation.current;
    };
  }, [identity]);
  const copy = async () => {
    const current = ++generation.current;
    const active = () =>
      current === generation.current && latestId.current === identity;
    setBusy(true);
    setMessage("");
    try {
      const text =
        ready?.id === identity
          ? ready.text
          : await completeKnowledgeText(id, active);
      if (!active()) return;
      setReady({ id: identity, text });
      try {
        await copyKnowledgeText(text);
        if (active()) {
          setReady(null);
          setRaw(false);
          setMessage("已复制完整 Markdown，可粘贴到笔记或 AI 工具。");
        }
      } catch {
        if (active()) {
          setRaw(ready?.id === identity);
          setMessage(
            ready?.id === identity
              ? "系统复制受限；下方是完整 Markdown，可选中后复制。"
              : "已完整读取；浏览器需要新的点击授权，请再次点击复制。",
          );
        }
      }
    } catch (error) {
      if (active())
        setMessage(
          error instanceof Error ? error.message : "复制失败，请重试。",
        );
    } finally {
      if (active()) setBusy(false);
    }
  };
  return (
    <>
      <Button variant="outline" disabled={busy} onClick={() => void copy()}>
        {busy
          ? "正在读取完整讲义…"
          : ready?.id === identity
            ? "复制已读取的完整 Markdown"
            : "复制完整 Markdown"}
      </Button>
      {message && <span role="status">{message}</span>}
      {raw && ready?.id === identity && (
        <textarea
          aria-label="完整 Markdown 复制备选"
          readOnly
          value={ready.text}
          rows={8}
          onFocus={(event) => event.currentTarget.select()}
        />
      )}
    </>
  );
}
