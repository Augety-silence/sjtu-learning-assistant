import {
  ArrowLeft,
  Bot,
  CalendarClock,
  Check,
  FileSearch,
  FolderTree,
  GraduationCap,
  Mail,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import appLogo from "@/assets/app-logo.png";
import { Button } from "@/components/ui/Button";
import type { AIAgentPreset, AIChatSessionSummary } from "@/lib/types";

const capabilities = [
  { label: "课程", tools: ["list_courses"], icon: GraduationCap },
  {
    label: "课程文件",
    tools: ["search_course_files", "list_course_files"],
    icon: FileSearch,
  },
  { label: "截止日期", tools: ["get_deadlines"], icon: CalendarClock },
  {
    label: "消息",
    tools: ["search_messages", "get_message_detail"],
    icon: Mail,
  },
  { label: "资料树", tools: ["get_material_tree"], icon: FolderTree },
];

interface SessionGroup {
  label: string;
  items: AIChatSessionSummary[];
}

function groupSessions(sessions: AIChatSessionSummary[]): SessionGroup[] {
  const groups: Record<string, AIChatSessionSummary[]> = {
    今天: [],
    昨天: [],
    "7 天内": [],
    更早: [],
  };
  const now = new Date();
  const today = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  ).getTime();
  for (const session of sessions) {
    const timestamp = new Date(
      session.updated_at ?? session.created_at ?? 0,
    ).getTime();
    const age = Number.isNaN(timestamp)
      ? Number.POSITIVE_INFINITY
      : today - timestamp;
    if (age < 24 * 60 * 60 * 1000) groups["今天"].push(session);
    else if (age < 2 * 24 * 60 * 60 * 1000) groups["昨天"].push(session);
    else if (age < 7 * 24 * 60 * 60 * 1000) groups["7 天内"].push(session);
    else groups["更早"].push(session);
  }
  return Object.entries(groups)
    .filter(([, items]) => items.length > 0)
    .map(([label, items]) => ({ label, items }));
}

export function AIChatSidebar({
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
  busy,
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
  busy: boolean;
}) {
  const selectedPreset =
    presets.find((preset) => preset.id === selectedPresetId) ?? null;
  const allowedTools = new Set(selectedPreset?.allowed_tools ?? []);
  const groups = groupSessions(sessions);

  return (
    <aside
      className={open ? "ai-sidebar is-open" : "ai-sidebar"}
      aria-label="AI Chat 导航"
    >
      <header className="ai-sidebar-brand">
        <button
          type="button"
          className="ai-brand-button"
          onClick={onBack}
          aria-label="返回学习助手"
        >
          <img src={appLogo} alt="" />
          <span>
            <strong>SJTU</strong>
            <small>Learning Agent</small>
          </span>
        </button>
        <Button
          variant="ghost"
          size="icon"
          className="ai-sidebar-close"
          aria-label="关闭对话导航"
          onClick={onClose}
        >
          <X />
        </Button>
      </header>

      <div className="ai-sidebar-actions">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft aria-hidden="true" />
          返回工作台
        </Button>
        <Button size="sm" onClick={onNew} disabled={busy || !selectedPresetId}>
          <Plus aria-hidden="true" />
          新对话
        </Button>
      </div>

      <section className="ai-agent-section">
        <div className="ai-sidebar-label">
          <span>选择 Agent</span>
          <Bot aria-hidden="true" />
        </div>
        <div
          className="ai-preset-list"
          role="radiogroup"
          aria-label="Agent 预设"
        >
          {presets.map((preset) => (
            <button
              key={preset.id}
              type="button"
              role="radio"
              aria-checked={preset.id === selectedPresetId}
              className={
                preset.id === selectedPresetId
                  ? "ai-preset-card is-selected"
                  : "ai-preset-card"
              }
              disabled={busy}
              onClick={() => onSelectPreset(preset.id)}
            >
              <span className="ai-preset-avatar">
                <Bot aria-hidden="true" />
              </span>
              <span className="ai-preset-copy">
                <strong>{preset.name}</strong>
                <small>{preset.description}</small>
                <em>{preset.allowed_tools.length} 个只读工具</em>
              </span>
              {preset.id === selectedPresetId && <Check aria-hidden="true" />}
            </button>
          ))}
        </div>

        <div className="ai-capability-card">
          <strong>当前可查询</strong>
          <div>
            {capabilities.map(({ label, tools, icon: Icon }) => {
              const enabled = tools.some((tool) => allowedTools.has(tool));
              return (
                <span
                  key={label}
                  className={enabled ? "is-enabled" : "is-disabled"}
                  title={
                    enabled
                      ? `${label}查询已启用`
                      : `${label}查询未在此 Agent 中启用`
                  }
                >
                  <Icon aria-hidden="true" />
                  {label}
                </span>
              );
            })}
          </div>
          <p>只读取本机已同步数据，不会执行提交、删除或修改。</p>
        </div>
      </section>

      <section className="ai-history-section">
        <div className="ai-sidebar-label">
          <span>历史会话</span>
          <small>{sessions.length}</small>
        </div>
        <div className="ai-history-list">
          {sessions.length === 0 && <p>还没有历史会话</p>}
          {groups.map((group) => (
            <div className="ai-history-group" key={group.label}>
              <h3>{group.label}</h3>
              {group.items.map((session) => (
                <div
                  className={
                    session.id === activeSessionId
                      ? "ai-history-item active"
                      : "ai-history-item"
                  }
                  key={session.id}
                >
                  <button
                    type="button"
                    onClick={() => onOpenSession(session.id)}
                  >
                    <strong>{session.title}</strong>
                    <span>
                      {presets.find((preset) => preset.id === session.preset_id)
                        ?.name ?? session.preset_id}
                    </span>
                  </button>
                  <button
                    type="button"
                    aria-label={`删除对话：${session.title}`}
                    onClick={() => onDeleteSession(session.id)}
                  >
                    <Trash2 aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
      </section>
    </aside>
  );
}
