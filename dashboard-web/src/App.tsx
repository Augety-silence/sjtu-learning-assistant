import { motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { AIChatView } from "@/components/AIChatView";
import { AppShell } from "@/components/AppShell";
import { AssignmentsView } from "@/components/AssignmentsView";
import { BackupView } from "@/components/BackupView";
import { DeadlinesView } from "@/components/DeadlinesView";
import { MaterialsView } from "@/components/MaterialsView";
import { MessagesView } from "@/components/MessagesView";
import { OverviewView } from "@/components/OverviewView";
import { SettingsView } from "@/components/SettingsView";
import { useToast } from "@/components/Toast";
import { getSettings, invoke } from "@/lib/api";
import { applyThemeMode } from "@/lib/theme";
import type { SyncStatus, ThemeMode, ViewName } from "@/lib/types";

const views: ViewName[] = [
  "overview",
  "deadlines",
  "messages",
  "assignments",
  "materials",
  "backup",
  "ai-chat",
  "settings",
];

function initialView(): ViewName {
  const value = window.location.hash.replace("#/", "") as ViewName;
  return views.includes(value) ? value : "overview";
}

export default function App() {
  const [view, setViewState] = useState<ViewName>(initialView);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [syncRequested, setSyncRequested] = useState(false);
  const [dataVersion, setDataVersion] = useState(0);
  const [themeMode, setThemeMode] = useState<ThemeMode>("system");
  const { showToast } = useToast();
  const shouldReduceMotion = useReducedMotion();

  const setView = (next: ViewName) => {
    window.location.hash = `/${next}`;
    setViewState(next);
  };
  useLayoutEffect(() => applyThemeMode(themeMode), [themeMode]);

  useEffect(() => {
    let active = true;
    void getSettings()
      .then((settings) => {
        if (active) setThemeMode(settings.theme_mode);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const onHash = () => setViewState(initialView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const next = await invoke<SyncStatus>("sync_status");
      setSyncStatus(next);
      if (syncRequested && next.status === "idle") {
        setSyncRequested(false);
        showToast({
          id: "sync-operation",
          kind: next.last_run_status === "success" ? "success" : "error",
          message:
            next.last_run_status === "success"
              ? "同步完成，数据已更新。"
              : "同步失败，请查看最近运行状态。",
        });
        setDataVersion((value) => value + 1);
      }
    } catch {
      setSyncStatus(null);
    }
  }, [showToast, syncRequested]);

  useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(
      () => void loadStatus(),
      syncRequested || syncStatus?.status === "syncing" ? 1200 : 5000,
    );
    return () => window.clearInterval(timer);
  }, [loadStatus, syncRequested, syncStatus?.status]);

  const triggerSync = async () => {
    setSyncRequested(true);
    showToast({
      id: "sync-operation",
      kind: "info",
      message: "正在提交同步请求…",
      duration: 0,
    });
    try {
      const result = await invoke<{ status: string }>("sync_trigger");
      showToast({
        id: "sync-operation",
        kind: "info",
        message:
          result.status === "already_running"
            ? "同步已在运行。"
            : "同步请求已接受，正在后台执行。",
        duration: 0,
      });
    } catch (reason) {
      setSyncRequested(false);
      showToast({
        id: "sync-operation",
        kind: "error",
        message: reason instanceof Error ? reason.message : "同步触发失败",
      });
    }
  };

  const syncing = syncRequested || syncStatus?.status === "syncing";
  if (view === "ai-chat") {
    return <AIChatView onBack={() => setView("overview")} />;
  }
  return (
    <AppShell
      view={view}
      setView={setView}
      drawerOpen={drawerOpen}
      setDrawerOpen={setDrawerOpen}
      syncStatus={syncStatus}
      syncing={syncing}
      onSync={() => void triggerSync()}
    >
      <motion.div
        key={view}
        className="view-transition"
        initial={
          shouldReduceMotion ? { opacity: 0.01 } : { opacity: 0.01, y: 4 }
        }
        animate={{ opacity: 1, y: 0 }}
        transition={{
          duration: shouldReduceMotion ? 0.08 : 0.18,
          ease: [0.16, 1, 0.3, 1],
        }}
      >
        {view === "overview" && (
          <OverviewView key={dataVersion} navigate={setView} />
        )}
        {view === "deadlines" && <DeadlinesView key={dataVersion} />}
        {view === "messages" && <MessagesView key={dataVersion} />}
        {view === "assignments" && <AssignmentsView key={dataVersion} />}
        {view === "materials" && <MaterialsView key={dataVersion} />}
        {view === "backup" && <BackupView />}
        {view === "settings" && (
          <SettingsView
            onArchiveChanged={() => setDataVersion((value) => value + 1)}
            onThemeModeChange={setThemeMode}
          />
        )}
      </motion.div>
    </AppShell>
  );
}
