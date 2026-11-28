import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  FolderOpen,
  Plus,
  Search,
  Server,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { AIFloatingMenu } from "@/components/AIFloatingMenu";
import type { AIAgentPreset, AIChatSessionSummary } from "@/lib/types";
import { V2Brand } from "@/v2/Shell";

export type AIWorkspacePage =
  | "skills"
  | "mcp"
  | "workspace"
  | "tasks"
  | "settings";

export function AIWorkspaceNavigation({
  open,
  onClose,
  onBack,
  onNew,
  presets,
  selectedPresetId,
  onSelectPreset,
  sessions,
  activeSessionId,
  onOpenSession,
  onDeleteSession,
  page,
  onPage,
}: {
  open: boolean;
  onClose: () => void;
  onBack: () => void;
  onNew: () => void;
  presets: AIAgentPreset[];
  selectedPresetId: string;
  onSelectPreset: (id: string) => void;
  sessions: AIChatSessionSummary[];
  activeSessionId?: string;
  onOpenSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  page: AIWorkspacePage | null;
  onPage: (page: AIWorkspacePage) => void;
}) {
  const [picker, setPicker] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const selected = presets.find((p) => p.id === selectedPresetId);
  useEffect(() => {
    if (picker)
      menu.current
        ?.querySelector<HTMLElement>('[aria-selected="true"]')
        ?.focus();
  }, [picker]);
  const selectPage = (next: AIWorkspacePage) => {
    setPicker(false);
    onPage(next);
    onClose();
  };
  return (
    <aside
      className={
        open ? "ai-sidebar ui2-sidebar is-open" : "ai-sidebar ui2-sidebar"
      }
      aria-label="AI 助手导航"
    >
      <header className="ai-sidebar-brand app-brand-header">
        <V2Brand onClick={onBack} />
        <button
          type="button"
          className="ai-sidebar-close"
          aria-label="关闭对话导航"
          onClick={onClose}
        >
          <X />
        </button>
      </header>
      <div className="ui2-sidebar-actions">
        <button type="button" onClick={onBack}>
          <ArrowLeft />
          返回工作台
        </button>
        <button
          type="button"
          className="is-primary"
          disabled={!selectedPresetId}
          onClick={() => {
            onNew();
            onClose();
          }}
        >
          <Plus />
          新对话
        </button>
      </div>
      <button
        ref={trigger}
        type="button"
        className="ui2-agent-row"
        aria-label={`当前 Agent：${selected?.name ?? "未选择"}`}
        aria-haspopup="listbox"
        aria-expanded={picker}
        onClick={() => setPicker(!picker)}
      >
        <img src={aiAgentLogo} alt="" />
        <span>{selected?.name ?? "选择助理"}</span>
        <ChevronDown />
      </button>
      {picker && (
        <AIFloatingMenu anchorRef={trigger} onClose={() => setPicker(false)}>
          <div
            ref={menu}
            role="listbox"
            aria-label="切换 Agent"
            onKeyDown={(event) => {
              const nodes = Array.from(
                menu.current?.querySelectorAll<HTMLButtonElement>(
                  '[role="option"]',
                ) ?? [],
              );
              const index = nodes.indexOf(
                document.activeElement as HTMLButtonElement,
              );
              const direction =
                event.key === "ArrowDown"
                  ? 1
                  : event.key === "ArrowUp"
                    ? -1
                    : 0;
              if (direction || event.key === "Home" || event.key === "End") {
                event.preventDefault();
                nodes[
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? nodes.length - 1
                      : (index + direction + nodes.length) % nodes.length
                ]?.focus();
              }
            }}
          >
            {presets.map((p) => (
              <button
                key={p.id}
                type="button"
                role="option"
                aria-selected={p.id === selectedPresetId}
                onClick={() => {
                  onSelectPreset(p.id);
                  setPicker(false);
                  trigger.current?.focus();
                }}
              >
                <img src={aiAgentLogo} alt="" />
                <span>{p.name}</span>
                {p.id === selectedPresetId && <Check />}
              </button>
            ))}
          </div>
        </AIFloatingMenu>
      )}
      <div className="ui2-config-bars">
        {(
          [
            { id: "skills", label: "技能配置", icon: Sparkles },
            { id: "mcp", label: "MCP 服务", icon: Server },
            { id: "workspace", label: "工作区", icon: FolderOpen },
          ] as const
        ).map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            className={page === id ? "is-active" : ""}
            aria-current={page === id ? "page" : undefined}
            onClick={() => selectPage(id)}
          >
            <Icon />
            <span>{label}</span>
            <ChevronRight />
          </button>
        ))}
      </div>
      <button
        type="button"
        className="ui2-all-tasks"
        onClick={() => selectPage("tasks")}
      >
        <Search />
        <span>全部任务</span>
        <Search />
      </button>
      <section className="ai-history-section">
        <div className="ai-sidebar-label">
          <span>最近会话</span>
          <small>{Math.min(8, sessions.length)}</small>
        </div>
        <div className="ai-history-list">
          {sessions.length === 0 && <p>还没有历史会话</p>}
          {sessions.slice(0, 8).map((session) => (
            <div
              className={
                session.id === activeSessionId && !page
                  ? "ai-history-item active"
                  : "ai-history-item"
              }
              key={session.id}
            >
              <button
                type="button"
                title={session.title}
                aria-current={
                  session.id === activeSessionId && !page ? "true" : undefined
                }
                onClick={() => {
                  onOpenSession(session.id);
                  onClose();
                }}
              >
                <strong>{session.title}</strong>
              </button>
              <button
                type="button"
                aria-label={`删除对话：${session.title}`}
                onClick={() => onDeleteSession(session.id)}
              >
                <Trash2 />
              </button>
            </div>
          ))}
        </div>
      </section>
      <button
        type="button"
        className="ui2-settings-entry"
        onClick={() => selectPage("settings")}
      >
        <Settings2 />
        个性化设置
      </button>
    </aside>
  );
}
