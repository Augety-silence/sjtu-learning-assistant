import {
  CalendarDays,
  FolderOpen,
  Home,
  type LucideIcon,
  Mail,
  RefreshCw,
  Settings,
} from "lucide-react";
import appLogo from "@/assets/app-logo.png";
import { Button } from "@/components/ui/Button";
import type { SyncStatus, ViewName } from "@/lib/types";

const navigation: Array<{ id: ViewName; label: string; icon: LucideIcon }> = [
  { id: "overview", label: "概览", icon: Home },
  { id: "deadlines", label: "截止事项", icon: CalendarDays },
  { id: "messages", label: "消息", icon: Mail },
  { id: "materials", label: "课程资料", icon: FolderOpen },
  { id: "settings", label: "设置", icon: Settings },
];

interface AppShellProps {
  view: ViewName;
  setView: (view: ViewName) => void;
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  syncStatus: SyncStatus | null;
  syncing: boolean;
  onSync: () => void;
  children: React.ReactNode;
}

function NavItems({
  view,
  select,
}: {
  view: ViewName;
  select: (view: ViewName) => void;
}) {
  return (
    <nav aria-label="主导航">
      {navigation.map((item) => {
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            type="button"
            className={`nav-item ${view === item.id ? "nav-selected" : ""}`}
            aria-current={view === item.id ? "page" : undefined}
            onClick={() => select(item.id)}
          >
            <Icon aria-hidden="true" />
            <span>{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

export function AppShell({
  view,
  setView,
  drawerOpen,
  setDrawerOpen,
  syncStatus,
  syncing,
  onSync,
  children,
}: AppShellProps) {
  const select = (next: ViewName) => {
    setView(next);
    setDrawerOpen(false);
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src={appLogo} alt="SJTU 学习助手标志" />
          <div>
            <p>SJTU</p>
            <span>学习助手</span>
          </div>
        </div>
        <NavItems view={view} select={select} />
        <div className="sidebar-footer">
          <span className={`status-dot ${syncing ? "status-running" : ""}`} />
          <span>{syncing ? "正在同步" : "本地运行"}</span>
        </div>
      </aside>
      {drawerOpen && (
        <div className="drawer-layer">
          <button
            type="button"
            className="drawer-mask"
            aria-label="关闭导航"
            onClick={() => setDrawerOpen(false)}
          />
          <aside className="mobile-drawer">
            <div className="brand">
              <img
                className="brand-mark"
                src={appLogo}
                alt="SJTU 学习助手标志"
              />
              <div>
                <p>SJTU</p>
                <span>学习助手</span>
              </div>
            </div>
            <NavItems view={view} select={select} />
            <Button
              variant="ghost"
              className="mt-auto"
              onClick={() => setDrawerOpen(false)}
            >
              关闭
            </Button>
          </aside>
        </div>
      )}
      <main className="workspace">
        <header className="page-header">
          <div className="page-heading">
            <Button
              className="mobile-menu"
              variant="outline"
              size="sm"
              onClick={() => setDrawerOpen(true)}
            >
              菜单
            </Button>
            <div>
              <h1>{navigation.find((item) => item.id === view)?.label}</h1>
              <p>
                上次成功同步：
                {syncStatus?.last_success_at
                  ? new Date(syncStatus.last_success_at).toLocaleString(
                      "zh-CN",
                      { timeZone: "Asia/Shanghai" },
                    )
                  : "暂无记录"}
              </p>
            </div>
          </div>
          <Button onClick={onSync} disabled={syncing}>
            <RefreshCw
              aria-hidden="true"
              className={syncing ? "animate-spin" : ""}
            />
            {syncing ? "同步中" : "立即同步"}
          </Button>
        </header>
        <div className="content">{children}</div>
      </main>
    </div>
  );
}
