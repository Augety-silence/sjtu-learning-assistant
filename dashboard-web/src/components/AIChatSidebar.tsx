import {
  ArrowLeft,
  CalendarClock,
  Check,
  ChevronDown,
  FileSearch,
  FolderTree,
  GraduationCap,
  Mail,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import appLogo from "@/assets/app-logo.png";
import { AIChatSettingsPanel } from "@/components/AIChatSettingsPanel";
import { Button } from "@/components/ui/Button";
import type {
  AIAgentPreset,
  AIChatPreferences,
  AIChatSessionSummary,
} from "@/lib/types";

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
    const sessionDate = new Date(timestamp);
    const sessionDay = new Date(
      sessionDate.getFullYear(),
      sessionDate.getMonth(),
      sessionDate.getDate(),
    ).getTime();
    const dayAge = Number.isNaN(sessionDay)
      ? Number.POSITIVE_INFINITY
      : Math.floor((today - sessionDay) / (24 * 60 * 60 * 1000));
    if (dayAge <= 0) groups["今天"].push(session);
    else if (dayAge === 1) groups["昨天"].push(session);
    else if (dayAge < 7) groups["7 天内"].push(session);
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
  preferences,
  settingsSaving,
  settingsStatus,
  onPreferenceChange,
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
  preferences: AIChatPreferences;
  settingsSaving: boolean;
  settingsStatus: string | null;
  onPreferenceChange: (change: Partial<AIChatPreferences>) => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const selectedPreset =
    presets.find((preset) => preset.id === selectedPresetId) ?? null;
  const allowedTools = new Set(selectedPreset?.allowed_tools ?? []);
  const enabledCapabilities = capabilities.filter(({ tools }) =>
    tools.some((tool) => allowedTools.has(tool)),
  );
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
          <img src={appLogo} alt="" aria-hidden="true" />
          <span>
            <strong>SJTU</strong>
            <small>学习助手</small>
          </span>
        </button>
        <Button
          variant="ghost"
          size="icon"
          className="ai-sidebar-close"
          aria-label="关闭对话导航"
          onClick={onClose}
        >
          <X aria-hidden="true" />
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

      <section className="ai-current-agent">
        <button
          type="button"
          className="ai-current-agent-trigger"
          aria-label={`当前 Agent：${selectedPreset?.name ?? "未选择"}`}
          aria-expanded={pickerOpen}
          aria-controls="ai-sidebar-agent-list"
          disabled={busy}
          onClick={() => setPickerOpen((open) => !open)}
        >
          <span className="ai-current-agent-avatar">
            <img src={aiAgentLogo} alt="" aria-hidden="true" />
          </span>
          <span>
            <small>当前 Agent</small>
            <strong>{selectedPreset?.name ?? "选择 Agent"}</strong>
          </span>
          <ChevronDown aria-hidden="true" />
        </button>
        {pickerOpen && (
          <div
            id="ai-sidebar-agent-list"
            className="ai-sidebar-agent-menu"
            role="listbox"
            aria-label="切换 Agent"
          >
            {presets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                role="option"
                aria-selected={preset.id === selectedPresetId}
                onClick={() => {
                  onSelectPreset(preset.id);
                  setPickerOpen(false);
                }}
              >
                <img src={aiAgentLogo} alt="" aria-hidden="true" />
                <span>
                  <strong>{preset.name}</strong>
                  <small>{preset.description}</small>
                </span>
                {preset.id === selectedPresetId && <Check aria-hidden="true" />}
              </button>
            ))}
          </div>
        )}
        <p className="ai-current-agent-description">
          {selectedPreset?.description ?? "选择一个 Agent 开始处理学习任务。"}
        </p>
        <div className="ai-capability-strip" aria-label="当前 Agent 功能">
          {enabledCapabilities.length > 0 ? (
            enabledCapabilities.map(({ label, icon: Icon }) => (
              <span key={label}>
                <Icon aria-hidden="true" />
                {label}
              </span>
            ))
          ) : (
            <small>暂未配置可用功能</small>
          )}
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
                    title={session.title}
                    onClick={() => onOpenSession(session.id)}
                  >
                    <strong>{session.title}</strong>
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

      <AIChatSettingsPanel
        preferences={preferences}
        saving={settingsSaving}
        status={settingsStatus}
        onChange={onPreferenceChange}
      />
    </aside>
  );
}
