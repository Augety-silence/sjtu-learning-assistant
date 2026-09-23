import {
  CalendarDays,
  ClipboardCheck,
  FolderOpen,
  Home,
  type LucideIcon,
  Mail,
  RefreshCw,
  Settings,
} from "lucide-react";
import { type RefObject, useEffect, useRef, useState } from "react";
import appLogo from "@/assets/app-logo.png";
import { Button } from "@/components/ui/Button";
import type { SyncStatus, ViewName } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

const navigation: Array<{ id: ViewName; label: string; icon: LucideIcon }> = [
  { id: "overview", label: "概览", icon: Home },
  { id: "deadlines", label: "截止事项", icon: CalendarDays },
  { id: "messages", label: "消息", icon: Mail },
  { id: "assignments", label: "作业中心", icon: ClipboardCheck },
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
  currentItemRef,
}: {
  view: ViewName;
  select: (view: ViewName) => void;
  currentItemRef?: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <nav aria-label="主导航">
      {navigation.map((item) => {
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            ref={view === item.id ? currentItemRef : undefined}
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

function MobileDrawer({
  view,
  select,
  close,
  menuButtonRef,
  shouldRestoreFocus,
}: {
  view: ViewName;
  select: (view: ViewName) => void;
  close: () => void;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
  shouldRestoreFocus: () => boolean;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const currentItemRef = useRef<HTMLButtonElement>(null);

  useModalFocus(drawerRef, close, {
    initialFocusRef: currentItemRef,
    triggerRef: menuButtonRef,
    shouldRestoreFocus,
  });

  return (
    <div className="drawer-layer" data-modal-layer>
      <button
        type="button"
        className="drawer-mask"
        aria-label="关闭导航"
        onClick={close}
      />
      <aside
        ref={drawerRef}
        className="mobile-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="移动导航"
        tabIndex={-1}
      >
        <div className="brand">
          <img className="brand-mark" src={appLogo} alt="SJTU 学习助手标志" />
          <div>
            <p>SJTU</p>
            <span>学习助手</span>
          </div>
        </div>
        <NavItems view={view} select={select} currentItemRef={currentItemRef} />
        <Button variant="ghost" className="mt-auto" onClick={close}>
          关闭
        </Button>
      </aside>
    </div>
  );
}

function mobileViewport() {
  return typeof window.matchMedia === "function"
    ? !window.matchMedia("(min-width: 600px)").matches
    : window.innerWidth < 600;
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
  const [isMobile, setIsMobile] = useState(mobileViewport);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousViewRef = useRef(view);
  const navigatingRef = useRef(false);

  useEffect(() => {
    const updateViewport = () => {
      const mobile = mobileViewport();
      setIsMobile(mobile);
      if (!mobile) setDrawerOpen(false);
    };
    const media =
      typeof window.matchMedia === "function"
        ? window.matchMedia("(min-width: 600px)")
        : null;

    media?.addEventListener?.("change", updateViewport);
    window.addEventListener("resize", updateViewport);
    updateViewport();
    return () => {
      media?.removeEventListener?.("change", updateViewport);
      window.removeEventListener("resize", updateViewport);
    };
  }, [setDrawerOpen]);

  useEffect(() => {
    if (previousViewRef.current === view) return;
    previousViewRef.current = view;
    headingRef.current?.focus();
  }, [view]);

  useEffect(() => {
    if (!drawerOpen && navigatingRef.current) {
      navigatingRef.current = false;
      headingRef.current?.focus();
    }
  }, [drawerOpen, view]);

  const closeDrawer = () => {
    navigatingRef.current = false;
    setDrawerOpen(false);
  };
  const select = (next: ViewName) => {
    navigatingRef.current = drawerOpen;
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
      {drawerOpen && isMobile && (
        <MobileDrawer
          view={view}
          select={select}
          close={closeDrawer}
          menuButtonRef={menuButtonRef}
          shouldRestoreFocus={() => !navigatingRef.current}
        />
      )}
      <main className="workspace">
        <header className="page-header">
          <div className="page-heading">
            <Button
              ref={menuButtonRef}
              className="mobile-menu"
              variant="outline"
              size="sm"
              aria-expanded={drawerOpen && isMobile}
              aria-haspopup="dialog"
              onClick={() => {
                navigatingRef.current = false;
                setDrawerOpen(true);
              }}
            >
              菜单
            </Button>
            <div>
              <h1 ref={headingRef} tabIndex={-1}>
                {navigation.find((item) => item.id === view)?.label}
              </h1>
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
