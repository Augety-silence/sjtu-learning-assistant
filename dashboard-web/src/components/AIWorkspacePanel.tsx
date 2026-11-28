import { ArrowLeft, ChevronRight, FolderOpen, Search } from "lucide-react";
import { useState } from "react";
import { AIChatSettingsPanel } from "@/components/AIChatSettingsPanel";
import { AIExtensionSkills } from "@/components/AIExtensionSkills";
import { AIExternalMCP } from "@/components/AIExternalMCP";
import type { AIWorkspacePage } from "@/components/AIWorkspaceNavigation";
import { UIReleaseToggle } from "@/components/UIReleaseToggle";
import type {
  AIAgentPresetsResult,
  AIChatPreferences,
  AIChatSessionSummary,
  AIExtensionSkill,
  AIWorkspaceStatus,
} from "@/lib/types";

const labels: Record<AIWorkspacePage, string> = {
  skills: "技能配置",
  mcp: "MCP 服务",
  workspace: "工作区",
  tasks: "全部任务",
  settings: "个性化设置",
};

export function AIWorkspacePanel({
  page,
  onClose,
  skills,
  onSkillsSaved,
  sessions,
  onOpenSession,
  workspace,
  workspaceBusy,
  workspaceError,
  onPickWorkspace,
  onClearWorkspace,
  preferences,
  settingsSaving,
  settingsStatus,
  onPreferenceChange,
}: {
  page: AIWorkspacePage;
  onClose: () => void;
  skills: AIExtensionSkill[];
  onSkillsSaved: (
    result: AIAgentPresetsResult,
    selectedId: string | null,
  ) => void;
  sessions: AIChatSessionSummary[];
  onOpenSession: (id: string) => void;
  workspace: AIWorkspaceStatus | null;
  workspaceBusy: boolean;
  workspaceError: string | null;
  onPickWorkspace: () => void;
  onClearWorkspace: () => void;
  preferences: AIChatPreferences;
  settingsSaving: boolean;
  settingsStatus: string | null;
  onPreferenceChange: (change: Partial<AIChatPreferences>) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = sessions.filter((s) =>
    s.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  return (
    <section className="ui2-config-page" aria-label={labels[page]}>
      <header className="ui2-config-header">
        <button type="button" aria-label="返回对话" onClick={onClose}>
          <ArrowLeft />
        </button>
        <h1>{labels[page]}</h1>
        <UIReleaseToggle />
      </header>
      <div className="ui2-config-scroll">
        {page === "skills" && (
          <AIExtensionSkills
            skills={skills}
            onChanged={(result) => onSkillsSaved(result, null)}
          />
        )}
        {page === "mcp" && <AIExternalMCP />}
        {page === "workspace" && (
          <article className="ui2-service-card">
            <header>
              <div>
                <h2>
                  <FolderOpen />
                  本地工作区
                </h2>
                <p>读取文件、生成修改提议，确认后写入</p>
              </div>
              <span className="ui2-status">
                {workspace?.available ? "已授权" : "未设置"}
              </span>
            </header>
            {workspace?.path && (
              <p className="ui2-workspace-path">
                <code>{workspace.path}</code>
              </p>
            )}
            {workspace?.reason && <p role="alert">{workspace.reason}</p>}
            {workspaceError && <p role="alert">{workspaceError}</p>}
            <div className="ui2-panel-actions">
              <button
                type="button"
                className="is-primary"
                disabled={workspaceBusy}
                onClick={onPickWorkspace}
              >
                {workspace?.configured ? "更换文件夹" : "选择文件夹"}
              </button>
              {workspace?.configured && (
                <button
                  type="button"
                  disabled={workspaceBusy}
                  onClick={onClearWorkspace}
                >
                  取消授权
                </button>
              )}
            </div>
            <p className="ui2-muted">
              支持 Markdown、TXT、CSV、JSON
              和代码等文本文件。修改先显示差异，应用前检查文件版本并备份。
            </p>
          </article>
        )}
        {page === "tasks" && (
          <>
            <label className="ui2-task-search">
              <Search />
              <input
                autoFocus
                type="search"
                aria-label="搜索全部任务"
                placeholder="搜索会话名称"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <div className="ui2-task-list">
              {filtered.map((s) => (
                <button
                  type="button"
                  key={s.id}
                  onClick={() => onOpenSession(s.id)}
                >
                  <span>
                    <strong>{s.title}</strong>
                    <small>
                      {s.updated_at
                        ? new Date(s.updated_at).toLocaleString("zh-CN")
                        : ""}
                    </small>
                  </span>
                  <ChevronRight />
                </button>
              ))}
              {filtered.length === 0 && (
                <p className="ui2-muted">没有匹配的会话</p>
              )}
            </div>
          </>
        )}
        {page === "settings" && (
          <AIChatSettingsPanel
            embedded
            preferences={preferences}
            saving={settingsSaving}
            status={settingsStatus}
            onChange={onPreferenceChange}
          />
        )}
      </div>
    </section>
  );
}
