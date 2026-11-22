import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AIChatSettingsPanel } from "@/components/AIChatSettingsPanel";
import { MessageDetailDialog } from "@/components/MessageDetailDialog";
import { RetainedView } from "@/components/RetainedView";
import { ToastProvider } from "@/components/Toast";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
import { VideosView } from "@/components/VideosView";
import "@/index.css";

const lesson = { id: "canvas-fixture", title: "缓存验收", courseName: "合成课程", source: "canvas" as const, playable: true };
function Fixture() {
  const [mail, setMail] = useState<string | null>(null);
  const [active, setActive] = useState(true);
  const target = useRef<HTMLDivElement>(null);
  const requests = useRef(0);
  return <ToastProvider><main style={{ padding: 20 }}>
    <button onClick={() => setMail("short")}>短邮件</button>
    <button onClick={() => setMail("long")}>长邮件</button>
    <button onClick={() => setActive(v => !v)}>切换页面</button>
    <button onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; }}>主题</button>
    <div style={{ position: "fixed", right: 12, top: 50, overflow: "hidden", width: 36, height: 36 }}><InfoTooltip label="边界说明">灰底白字的全局说明不会被父容器裁切，也不会被浮层覆盖。</InfoTooltip></div>
    <div style={{ position: "fixed", bottom: 20, left: 20, width: 360 }}>
      <div style={{ position: "absolute", bottom: 50 }}>背景文字背景文字背景文字背景文字背景文字背景文字背景文字背景文字</div>
      <AIChatSettingsPanel preferences={{ ai_chat_send_shortcut: "enter", ai_reply_language: "zh", ai_attachment_context_budget: "balanced", ai_auto_open_activity: false, ai_code_line_numbers: false }} saving={false} status={null} onChange={() => {}} />
    </div>
    {active && <div key="video-page" ref={target} />}
    <RetainedView active={active} target={target}><VideosView active={active} videos={[lesson]} onPlay={async () => {
      requests.current += 1;
      document.documentElement.dataset.playRequests = String(requests.current);
      // A local synthetic movie is supplied by the native verification bridge.
      const bridge = (window as unknown as { pywebview: { api: { media: () => Promise<string> } } }).pywebview;
      return { url: await bridge.api.media() };
    }} /></RetainedView>
    {mail && <MessageDetailDialog item={{ source_id: mail, kind: "email", title: mail === "short" ? "短邮件" : "长邮件", source_label: "合成消息", occurred_at: null, is_unread: false, url: null }} onClose={() => setMail(null)} />}
  </main></ToastProvider>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
