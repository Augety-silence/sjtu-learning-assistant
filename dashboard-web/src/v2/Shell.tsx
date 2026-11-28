import {
  CalendarDays,
  ChevronDown,
  ClipboardCheck,
  FolderKanban,
  FolderOpen,
  HardDrive,
  Home,
  LibraryBig,
  Mail,
  Menu,
  MessageSquare,
  Plus,
  RefreshCw,
  Settings,
  Video,
  X,
} from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import logo from "@/assets/app-logo.png";
import { AIFloatingMenu } from "@/components/AIFloatingMenu";
import { MarkdownSyncProgress } from "@/components/MarkdownSyncProgress";
import type { SidebarAIRequest } from "@/components/SidebarAssistant";
import { invoke } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { SyncStatus, ViewName } from "@/lib/types";
import { V2Button, V2Dialog } from "./primitives";

const navigation = [
  { id: "overview", label: "概览", icon: Home },
  { id: "calendar", label: "日程", icon: CalendarDays },
  { id: "messages", label: "消息", icon: Mail },
  { id: "assignments", label: "作业中心", icon: ClipboardCheck },
  { id: "materials", label: "课程资料", icon: FolderOpen },
  { id: "videos", label: "课程视频", icon: Video },
  { id: "backup", label: "交大云盘", icon: HardDrive },
  { id: "knowledge", label: "课程知识库", icon: LibraryBig },
  { id: "ai-chat", label: "AI 助手", icon: MessageSquare },
] as const;
export const pageTitles: Record<string, string> = Object.fromEntries(
  [
    ...navigation,
    { id: "settings", label: "设置" },
    { id: "local-projects", label: "资料项目" },
  ].map((n) => [n.id, n.label]),
);
export function V2Brand({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      className="v2-brand"
      aria-label="返回学习助手"
      onClick={onClick}
    >
      <img src={logo} alt="SJTU 学习助手标志" />
      <span>
        <strong>SJTU</strong>
        <small>学习助手</small>
      </span>
    </button>
  );
}
export function V2Sync({
  status,
  syncing,
  onSync,
  onNavigate,
}: {
  status: SyncStatus | null;
  syncing: boolean;
  onSync: () => void;
  onNavigate: (view: "knowledge" | "settings") => void;
}) {
  const [open, setOpen] = useState(false);
  const [automation, setAutomation] = useState<{
    enabled: boolean;
    interval_seconds: number | null;
  } | null>(null);
  useEffect(() => {
    if (!open) return;
    let active = true;
    void invoke<{ enabled: boolean; interval_seconds: number | null }>(
      "sync_automation_status",
    )
      .then((data) => {
        if (active) setAutomation(data);
      })
      .catch(() => {
        if (active) setAutomation(null);
      });
    return () => {
      active = false;
    };
  }, [open]);
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        type="button"
        ref={trigger}
        className="v2-button"
        aria-expanded={open}
        aria-label="同步模块"
        onClick={() => setOpen(!open)}
      >
        <RefreshCw size={15} className={syncing ? "v2-spin" : ""} />
        {syncing ? "同步中" : "同步"}
        <ChevronDown size={14} />
      </button>
      {open && (
        <AIFloatingMenu
          anchorRef={trigger}
          onClose={() => setOpen(false)}
          width={360}
        >
          <div className="v2-sync">
            <h2>学习数据同步</h2>
            <p>Canvas 所有学期课程、作业、资料与邮件</p>
            <div className="v2-sync-automatic">
              <span className="v2-badge">
                {automation?.enabled ? "自动同步运行中" : "自动同步"}
              </span>
              <span>
                {automation
                  ? automation.enabled && automation.interval_seconds
                    ? `每 ${automation.interval_seconds / 60} 分钟同步一次`
                    : "定时任务未运行"
                  : "定时同步状态暂不可用"}
              </span>
            </div>
            <div className="v2-sync-status">
              <span>{syncing ? "正在同步学习数据" : "上次同步"}</span>
              <strong>{formatDateTime(status?.last_success_at ?? null)}</strong>
            </div>
            <V2Button disabled={syncing} className="primary" onClick={onSync}>
              {syncing ? "正在同步…" : "立即同步学习数据"}
            </V2Button>
            <MarkdownSyncProgress embedded onNavigate={onNavigate} />
          </div>
        </AIFloatingMenu>
      )}
    </>
  );
}
export function V2OperationTree() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <V2Button onClick={() => setOpen(true)}>功能操作树</V2Button>
      {open && (
        <V2Dialog title="功能操作树" onClose={() => setOpen(false)}>
          <div className="v2-operation-tree">
            {[
              {
                name: "概览",
                steps: [
                  "查看24小时内截止 → 作业中心",
                  "查看最新消息 → 消息详情",
                  "查看今日日程 → 日程",
                  "本周学习安排 → AI 助手",
                ],
              },
              {
                name: "日程",
                steps: [
                  "选择日期 → 查看日程详情",
                  "空白日历／添加日程 → 本机个人日程",
                  "导入ICS → 原课表同步",
                  "Canvas作业日程 → 作业中心",
                ],
              },
              {
                name: "消息",
                steps: [
                  "邮件／Canvas公告／Canvas作业 → 搜索、已读与详情",
                  "附件 → 本地打开／下载",
                ],
              },
              {
                name: "作业中心",
                steps: [
                  "课程及状态筛选 → 作业列表 → 详细要求",
                  "文件／文本／链接 → 确认提交 → Canvas回读",
                ],
              },
              {
                name: "课程资料",
                steps: [
                  "所有学期 → 课程目录 → 文件",
                  "下载／本地打开／Finder定位／预览",
                  "移动／恢复分类 → 原文件操作",
                ],
              },
              {
                name: "课程视频",
                steps: [
                  "课程选择 → 录像 → 流式播放",
                  "倍速／字幕跳转／全屏／PPT切换",
                  "进度记忆／字幕同步／课件PDF",
                ],
              },
              {
                name: "交大云盘",
                steps: [
                  "我的文件 → 浏览、搜索、上传、多选下载",
                  "本地同步 → 原备份与传输任务",
                  "冲突确认 → 原续传队列",
                ],
              },
              {
                name: "课程知识库",
                steps: [
                  "编译产物目录 → 已完成文档 → 阅读／本地打开",
                  "同步与编译队列 → 状态／重试／安全停止",
                ],
              },
              {
                name: "AI 助手",
                steps: [
                  "助理选择 → 新建／历史会话 → 流式对话",
                  "技能／MCP／工作区 → 中央配置",
                  "本地附件／文件修改提议 → 差异确认",
                  "Activity工具轨迹／补充指令／停止任务",
                ],
              },
              {
                name: "设置",
                steps: [
                  "账户、模型、语音与同步配置",
                  "本地目录／数据库／诊断／更新",
                ],
              },
            ].map((group) => (
              <details key={group.name} open>
                <summary>{group.name}</summary>
                <ul>
                  {group.steps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
        </V2Dialog>
      )}
    </>
  );
}
export function V2Shell({
  view,
  setView,
  drawerOpen,
  setDrawerOpen,
  syncStatus,
  syncing,
  onSync,
  onOpenAI,
  headerActions,
  children,
}: {
  view: ViewName;
  availableViews?: readonly ViewName[];
  setView: (view: ViewName) => void;
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  syncStatus: SyncStatus | null;
  syncing: boolean;
  onSync: () => void;
  onOpenAI: (request?: SidebarAIRequest) => void;
  headerActions?: ReactNode;
  children: ReactNode;
}) {
  const select = (id: ViewName) => {
    if (id === "ai-chat") onOpenAI();
    else setView(id);
    setDrawerOpen(false);
  };
  return (
    <div className="v2-shell" data-view={view}>
      <aside
        className={`v2-sidebar ${drawerOpen ? "open" : ""}`}
        aria-label="学习空间"
      >
        <V2Brand onClick={() => select("overview")} />
        <nav aria-label="主导航">
          {navigation.map((n) => (
            <button
              key={n.id}
              type="button"
              aria-label={n.label}
              aria-current={view === n.id ? "page" : undefined}
              onClick={() => select(n.id)}
            >
              <n.icon size={19} />
              <span>{n.label}</span>
            </button>
          ))}
        </nav>
        <footer>
          {[
            { id: "settings", label: "设置", icon: Settings },
            { id: "local-projects", label: "资料项目", icon: FolderKanban },
          ].map((n) => (
            <button
              type="button"
              key={n.id}
              aria-current={view === n.id ? "page" : undefined}
              onClick={() => select(n.id as ViewName)}
            >
              <n.icon size={19} />
              {n.label}
            </button>
          ))}
        </footer>
      </aside>
      {drawerOpen && (
        <button
          type="button"
          className="v2-nav-scrim"
          aria-label="关闭导航"
          onClick={() => setDrawerOpen(false)}
        >
          <X />
        </button>
      )}
      <div className="v2-main">
        <header className="v2-app-header">
          <div>
            <V2Button
              className="v2-mobile-menu"
              aria-label="打开导航"
              onClick={() => setDrawerOpen(true)}
            >
              <Menu size={18} />
            </V2Button>
            <span>学习空间</span>
            <i>/</i>
            <strong>{pageTitles[view]}</strong>
          </div>
          <div>
            <V2OperationTree />
            <V2Sync
              status={syncStatus}
              syncing={syncing}
              onSync={onSync}
              onNavigate={setView}
            />
          </div>
        </header>
        <main
          className={`v2-content v2-page-${view}`}
          id="main-content"
          tabIndex={-1}
        >
          <header className="v2-page-heading">
            <h1 tabIndex={-1}>{pageTitles[view]}</h1>
            <div>
              {view === "overview" ? (
                <V2Button
                  onClick={() => {
                    select("calendar");
                    window.setTimeout(
                      () =>
                        window.dispatchEvent(new Event("sjtu-v2-create-event")),
                      50,
                    );
                  }}
                >
                  <Plus />
                  添加日程
                </V2Button>
              ) : view !== "calendar" ? (
                headerActions
              ) : null}
            </div>
          </header>
          {children}
        </main>
      </div>
    </div>
  );
}
