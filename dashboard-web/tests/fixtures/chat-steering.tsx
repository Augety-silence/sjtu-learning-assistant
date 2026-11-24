import { MotionConfig } from "motion/react";
import { createRoot } from "react-dom/client";
import { AIChatView } from "@/components/AIChatView";
import { ToastProvider } from "@/components/Toast";
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

// 页面使用真实 AIChatView 和 api.ts；所有桥接动作由原生脚本的内存夹具处理。
const errors: string[] = [];
Object.assign(window, { __chatSteeringFixtureErrors: errors });
window.addEventListener("error", (event) => errors.push(event.message));
window.addEventListener("unhandledrejection", (event) =>
  errors.push(String(event.reason)),
);
document.documentElement.dataset.chatSteeringFixture = "true";
createRoot(document.getElementById("root")!).render(
  <MotionConfig reducedMotion="user">
    <ToastProvider>
      <AIChatView onBack={() => undefined} />
    </ToastProvider>
  </MotionConfig>,
);
