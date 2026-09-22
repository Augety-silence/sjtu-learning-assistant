import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DeadlinesView } from "@/components/DeadlinesView";
import { MaterialsView } from "@/components/MaterialsView";
import { MessagesView } from "@/components/MessagesView";
import { OverviewView } from "@/components/OverviewView";
import { SettingsView } from "@/components/SettingsView";
import { invoke } from "@/lib/api";
import type { MessageItem, SyncStatus, ViewName } from "@/lib/types";

const views: ViewName[] = [
  "overview",
  "deadlines",
  "messages",
  "materials",
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
  const [message, setMessage] = useState("");
  const [dataVersion, setDataVersion] = useState(0);
  const [pendingMessage, setPendingMessage] = useState<MessageItem | null>(
    null,
  );

  const setView = (next: ViewName) => {
    window.location.hash = `/${next}`;
    setViewState(next);
  };
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
        setMessage(
          next.last_run_status === "success"
            ? "同步完成，数据已更新。"
            : "同步已结束，请查看最近运行状态。",
        );
        setDataVersion((value) => value + 1);
      }
    } catch {
      setSyncStatus(null);
    }
  }, [syncRequested]);

  useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(
      () => void loadStatus(),
      syncRequested || syncStatus?.status === "syncing" ? 1200 : 5000,
    );
    return () => window.clearInterval(timer);
  }, [loadStatus, syncRequested, syncStatus?.status]);

  const triggerSync = async () => {
    setMessage("");
    setSyncRequested(true);
    try {
      const result = await invoke<{ status: string }>("sync_trigger");
      setMessage(
        result.status === "already_running"
          ? "同步已在运行。"
          : "同步请求已接受，正在后台执行。",
      );
    } catch (reason) {
      setSyncRequested(false);
      setMessage(reason instanceof Error ? reason.message : "同步触发失败");
    }
  };

  const openMessage = (item: MessageItem) => {
    setPendingMessage(item);
    setView("messages");
  };

  const syncing = syncRequested || syncStatus?.status === "syncing";
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
      {message && (
        <div className="global-notice" role="status">
          {message}
        </div>
      )}
      <div>
        {view === "overview" && (
          <OverviewView
            key={dataVersion}
            navigate={setView}
            openMessage={openMessage}
          />
        )}
        {view === "deadlines" && <DeadlinesView key={dataVersion} />}
        {view === "messages" && (
          <MessagesView
            key={dataVersion}
            pendingMessage={pendingMessage}
            onPendingMessageConsumed={() => setPendingMessage(null)}
          />
        )}
        {view === "materials" && <MaterialsView key={dataVersion} />}
        {view === "settings" && (
          <SettingsView
            onArchiveChanged={() => setDataVersion((value) => value + 1)}
          />
        )}
      </div>
    </AppShell>
  );
}
