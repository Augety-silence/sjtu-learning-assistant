import { useState } from "react";
import { createRoot } from "react-dom/client";
import { AppShell } from "@/components/AppShell";
import { ToastProvider } from "@/components/Toast";
import type { ViewName } from "@/lib/types";
import "@/index.css";

function Fixture() {
  const [view, setView] = useState<ViewName>("knowledge");
  const [drawerOpen, setDrawerOpen] = useState(false);
  return <ToastProvider><AppShell view={view} setView={setView} drawerOpen={drawerOpen}
    setDrawerOpen={setDrawerOpen} syncStatus={null} syncing={false} onSync={() => {}} onOpenAI={() => {}}>
    <section><h2>合成界面验收</h2><p>此页面不读取学习资料，不调用学校 API。</p></section>
  </AppShell></ToastProvider>;
}
const root = document.getElementById("root");
if (root) createRoot(root).render(<Fixture />);
