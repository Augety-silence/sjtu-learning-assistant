import {
  CalendarDays,
  ClipboardCheck,
  CloudUpload,
  FolderOpen,
  Home,
  type LucideIcon,
  Mail,
  MessageCircle,
  RefreshCw,
  Settings,
} from "lucide-react";
import { AnimatePresence, motion, useIsPresent } from "motion/react";
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
  { id: "backup", label: "云盘备份", icon: CloudUpload },
  { id: "ai-chat", label: "AI Chat", icon: MessageCircle },
  { id: "settings", label: "设置", icon: Settings },
];

const primaryNavigation = navigation.filter((item) => item.id !== "settings");
const utilityNavigation = navigation.filter((item) => item.id === "settings");

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
  items,
  label,
  view,
  select,
  currentItemRef,
}: {
  items: typeof navigation;
  label: string;
  view: ViewName;
  select: (view: ViewName) => void;
  currentItemRef?: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <nav aria-label={label} data-nav-group={label}>
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            ref={view === item.id ? currentItemRef : undefined}
            type="button"
            className={`nav-item ${view === item.id ? "nav-selected" : ""}`}
            aria-label={item.label}
            title={item.label}
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
  const isPresent = useIsPresent();

  useModalFocus(drawerRef, close, {
    initialFocusRef: currentItemRef,
    triggerRef: menuButtonRef,
    shouldRestoreFocus,
    active: isPresent,
  });

  return (
    <motion.div
      className="drawer-layer"
      data-modal-layer
      data-motion-layer="drawer"
      aria-hidden={isPresent ? undefined : true}
      initial={{ opacity: 0.01 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: isPresent ? 0.14 : 0.12 }}
    >
      <button
        type="button"
        className="drawer-mask"
        aria-label="关闭导航"
        disabled={!isPresent}
        onClick={close}
      />
      <motion.aside
        ref={drawerRef}
        className="mobile-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="移动导航"
        aria-hidden={isPresent ? undefined : true}
        data-motion-surface="drawer"
        tabIndex={-1}
        initial={{ opacity: 0.92, x: -12 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0.92, x: -10 }}
        transition={{
          duration: isPresent ? 0.22 : 0.16,
          ease: [0.16, 1, 0.3, 1],
        }}
      >
        <div className="brand">
          <img className="brand-mark" src={appLogo} alt="SJTU 学习助手标志" />
          <div>
            <p>SJTU</p>
            <span>学习助手</span>
          </div>
        </div>
        <div className="sidebar-scroll">
          <NavItems
            items={primaryNavigation}
            label="主导航"
            view={view}
            select={select}
            currentItemRef={currentItemRef}
          />
        </div>
        <div className="sidebar-lower">
          <NavItems
            items={utilityNavigation}
            label="辅助导航"
            view={view}
            select={select}
            currentItemRef={currentItemRef}
          />
          <Button variant="ghost" className="drawer-close" onClick={close}>
            关闭
          </Button>
        </div>
      </motion.aside>
    </motion.div>
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
        <div className="sidebar-scroll">
          <NavItems
            items={primaryNavigation}
            label="主导航"
            view={view}
            select={select}
          />
        </div>
        <div className="sidebar-lower">
          <NavItems
            items={utilityNavigation}
            label="辅助导航"
            view={view}
            select={select}
          />
          <div className="sidebar-footer" aria-live="polite">
            <span
              className={`status-dot ${syncing ? "status-running" : ""}`}
              aria-hidden="true"
            />
            <span>{syncing ? "正在同步" : "本地运行"}</span>
          </div>
        </div>
      </aside>
      <AnimatePresence initial={false}>
        {drawerOpen && isMobile && (
          <MobileDrawer
            view={view}
            select={select}
            close={closeDrawer}
            menuButtonRef={menuButtonRef}
            shouldRestoreFocus={() => !navigatingRef.current}
          />
        )}
      </AnimatePresence>
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
