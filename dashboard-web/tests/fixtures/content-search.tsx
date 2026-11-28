import { AIChatSettingsPanel } from "@/components/AIChatSettingsPanel";
import "../../src/v2/settings-controls.css";
import "../../src/selection-indicator.css";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { FloatingDocumentPreview } from "@/components/FloatingDocumentPreview";
import { FilePreviewContent } from "@/components/FilePreviewDialog";
import { VideoTranscript } from "@/components/VideoTranscript";
import { PdfSearchPreview } from "@/components/PdfSearchPreview";
import "../../src/index.css";
import "../../src/v2/design.css";
import "../../src/v2/features.css";
import "../../src/v2/video.css";
document.documentElement.dataset.uiRelease = "2";
window.addEventListener("error", (event) => { document.body.dataset.fixtureError = event.message; });
const paragraph = "建模与优化包含对偶理论、单纯形与列生成。";
function Fixture() {
  const [open, setOpen] = useState(false);
  const [time, setTime] = useState(2);
  return <main style={{ padding: 24, maxWidth: 1000, margin: "auto" }}>
    <section id="settings-controls" style={{ maxWidth: 520 }}>
      <div className="settings-row settings-toggle-row"><div className="heading-with-info"><label htmlFor="archive-toggle"><strong>下载课程资料</strong></label><button type="button" aria-label="说明">ⓘ</button></div><input id="archive-toggle" type="checkbox" role="switch" defaultChecked/></div>
      <AIChatSettingsPanel embedded preferences={{ ai_chat_send_shortcut: "cmd_enter", ai_reply_language: "auto", ai_attachment_context_budget: "balanced", ai_auto_open_activity: false, ai_code_line_numbers: true }} saving={false} status={null} onChange={() => {}} />
    </section>
    <button type="button" id="open-document" onClick={() => setOpen(true)}>打开文档</button>
    <section className="v2-video-learning expanded" style={{ marginTop: 16 }}>
      <h2 style={{ fontSize: 15 }}>在线观看 · 字幕</h2>
      <div className="video-tool-panel">
        <VideoTranscript sourceId="sample" cues={[
          { startMs: 1000, endMs: 3000, text: "第一段课程导入" },
          { startMs: 4000, endMs: 6000, text: "对偶理论与对偶变量" },
          { startMs: 7000, endMs: 9000, text: "列生成" },
        ]} playbackTime={time} onSeek={(seconds) => { setTime(seconds); document.body.dataset.seekSeconds = String(seconds); }}/>
      </div>
    </section>
    {open && <FloatingDocumentPreview title="搜索验收.md" onClose={() => setOpen(false)}>
      <FilePreviewContent preview={{ kind: "text", name: "搜索验收.md", text: `# 课程文档\n\n建模与**优化**\n\n${Array.from({ length: 35 }, () => paragraph).join("\n\n")}` }}/>
    </FloatingDocumentPreview>}
    <div style={{ height: 350, marginTop: 18 }}><PdfSearchPreview title="PDF 搜索" sourceUrl="about:blank" pages={[{ page: 1, text: "课程导入" }, { page: 2, text: "对偶理论" }]}/></div>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
