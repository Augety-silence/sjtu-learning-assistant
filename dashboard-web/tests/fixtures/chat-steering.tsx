import { MotionConfig } from "motion/react";
import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { createRoot } from "react-dom/client";
import { AIChatView } from "@/components/AIChatView";
import { ToastProvider } from "@/components/Toast";
import "@/v2/design.css";
import "@/v2/Schedule.css";
import "@/v2/features.css";
import "@/v2/video.css";
import "@/index.css";
import "@/aura-pages.css";
import "@/ai-chat-aura.css";
import "@/app-brand.css";
import "@/sidebar-assistant.css";
import "@/performance.css";
import "@/workspace-motion.css";
import "@/page-motion.css";
import "@/nav-indicator.css";
import "@/selection-indicator.css";
import "@/ui-2.css";

const visual = new URLSearchParams(location.search).has("visual");
if (visual) {
  const session = {id:"visual-session", title:"本周复习安排", model:"auto", thinking_depth:"standard", preset_id:"general", traces:[], messages:[{id:"visual-user",role:"user",content:"帮我梳理本周复习安排，并标注优先顺序。"},{id:"visual-answer",role:"assistant",content:"先处理即将截止的作业，再安排课程复习。\n\n### 建议安排\n\n1. 今天：整理机器学习作业的要求和现有进度。\n2. 明天：复习数据结构中尚未掌握的部分。\n3. 周末：整理笔记，补齐需要复看的课堂内容。\n\n执行前可以补充具体日期和可用时间。"}]};
  Object.assign(window, {pywebview:{api:{invoke:async(action:string) => {
    const data = action === "ai_presets" ? {default_preset_id:"general",items:[{id:"general",name:"综合学习助理",description:"协助学习与课程资料检索",allowed_tools:["list_courses"],is_default:true}]} : action === "ai_workspace_status" ? {configured:false,available:false,name:null,path:null,reason:null} : action === "ai_chat_sessions" ? {items:[session]} : action === "ai_chat_session" || action === "ai_chat_new" ? session : action === "settings_status" ? {ai_chat_send_shortcut:"enter",ai_reply_language:"zh",ai_attachment_context_budget:"balanced",ai_auto_open_activity:false,ai_code_line_numbers:false} : {};
    return {ok:true,data};
  }}}});
}
// 页面使用真实 AIChatView 和 api.ts；所有桥接动作由原生脚本的内存夹具处理。
const errors: string[] = [];
Object.assign(window, { __chatSteeringFixtureErrors: errors });
window.addEventListener("error", (event) => errors.push(event.message));
window.addEventListener("unhandledrejection", (event) =>
  errors.push(String(event.reason)),
);
document.documentElement.dataset.uiRelease = "2";
document.documentElement.dataset.theme = "light";
document.documentElement.dataset.chatSteeringFixture = "true";
function Fixture() {
  const [ai, setAI] = useState(visual);
  return ai ? <AIChatView onBack={() => setAI(false)} /> : <AppShell view="overview" setView={() => undefined} drawerOpen={false} setDrawerOpen={() => undefined} syncStatus={null} syncing={false} onSync={() => undefined} onOpenAI={() => setAI(true)}><p>原生布局验收</p></AppShell>;
}
createRoot(document.getElementById("root")!).render(
  <MotionConfig reducedMotion="user">
    <ToastProvider>
      <Fixture />
    </ToastProvider>
  </MotionConfig>,
);
