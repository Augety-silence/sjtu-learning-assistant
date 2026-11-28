import {
  Check,
  ChevronDown,
  FolderOpen,
  Paperclip,
  ShieldCheck,
  X,
} from "lucide-react";
import { useRef } from "react";
import type { Choice } from "@/components/AIComposerChoice";
import { AIFloatingMenu } from "@/components/AIFloatingMenu";
import type { AIModel, AIWorkspaceStatus } from "@/lib/types";
export function AIComposerFooter({
  workspace,
  workspaceBusy,
  workspaceError,
  onChangeWorkspace,
  onOpenWorkspace,
  attachmentBusy,
  attachmentCount,
  onPickAttachment,
  models,
  model,
  onModelChange,
  openMenu,
  setOpenMenu,
  length,
}: {
  workspace?: AIWorkspaceStatus | null;
  workspaceBusy: boolean;
  workspaceError?: string | null;
  onChangeWorkspace?: (action: "pick" | "clear") => void;
  onOpenWorkspace?: () => void;
  attachmentBusy: boolean;
  attachmentCount: number;
  onPickAttachment: () => void;
  models: Choice<AIModel>[];
  model: AIModel;
  onModelChange: (value: AIModel) => void;
  openMenu: string | null;
  setOpenMenu: (value: "workspace" | "permission" | null) => void;
  length: number;
}) {
  const workspaceTrigger = useRef<HTMLButtonElement>(null);
  const permissionTrigger = useRef<HTMLButtonElement>(null);
  return (
    <div className="ai-composer-footer">
      {
        <div className="ai-composer-controls">
          <button
            type="button"
            ref={workspaceTrigger}
            aria-haspopup="dialog"
            aria-expanded={openMenu === "workspace"}
            onClick={() =>
              setOpenMenu(openMenu === "workspace" ? null : "workspace")
            }
          >
            <FolderOpen />
            <span>{workspace?.available ? workspace.name : "选择工作区"}</span>
            <ChevronDown />
          </button>
          {openMenu === "workspace" && (
            <AIFloatingMenu
              anchorRef={workspaceTrigger}
              onClose={() => setOpenMenu(null)}
              width={320}
              placement="above"
              className="ai-composer-popover"
            >
              <section role="dialog" aria-label="当前对话配置">
                <header>当前对话配置</header>
                <div className="ai-workspace-summary">
                  <FolderOpen />
                  <span>
                    <strong>
                      {workspace?.available ? workspace.name : "未选择工作区"}
                    </strong>
                    <small>
                      {workspace?.available
                        ? "可读取此目录中的文件，修改需确认"
                        : "选择目录后可检索和编辑工作区文件"}
                    </small>
                  </span>
                </div>
                {workspaceError && <p role="alert">{workspaceError}</p>}
                <div className="ai-resource-actions">
                  <button
                    type="button"
                    disabled={workspaceBusy}
                    onClick={() => onChangeWorkspace?.("pick")}
                  >
                    <FolderOpen />
                    {workspaceBusy
                      ? "设置中…"
                      : workspace?.available
                        ? "更换工作区"
                        : "选择工作区"}
                  </button>
                  {workspace?.available && (
                    <button
                      type="button"
                      disabled={workspaceBusy}
                      onClick={() => onChangeWorkspace?.("clear")}
                    >
                      <X />
                      移除工作区
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={attachmentBusy || attachmentCount >= 20}
                    onClick={() => {
                      setOpenMenu(null);
                      onPickAttachment();
                    }}
                  >
                    <Paperclip />
                    添加本地文件
                  </button>
                  {workspace?.available && (
                    <button
                      type="button"
                      onClick={() => {
                        setOpenMenu(null);
                        onOpenWorkspace?.();
                      }}
                    >
                      <FolderOpen />
                      浏览工作区文件
                    </button>
                  )}
                </div>
                <fieldset className="ai-inline-models">
                  <legend>对话模型</legend>
                  {models.map((item) => (
                    <button
                      type="button"
                      key={item.value}
                      aria-pressed={model === item.value}
                      onClick={() => {
                        onModelChange(item.value);
                      }}
                    >
                      <span>{item.label}</span>
                      {model === item.value && <Check />}
                    </button>
                  ))}
                </fieldset>
              </section>
            </AIFloatingMenu>
          )}
          <button
            type="button"
            ref={permissionTrigger}
            aria-haspopup="dialog"
            aria-expanded={openMenu === "permission"}
            onClick={() =>
              setOpenMenu(openMenu === "permission" ? null : "permission")
            }
            title="读取已选择的文件和工作区；文件修改需要确认"
          >
            <ShieldCheck />
            修改前确认
            <ChevronDown />
          </button>
          {openMenu === "permission" && (
            <AIFloatingMenu
              anchorRef={permissionTrigger}
              onClose={() => setOpenMenu(null)}
              width={300}
              placement="above"
              className="ai-composer-popover"
            >
              <section role="dialog" aria-label="文件操作权限">
                <header>
                  <ShieldCheck />
                  修改前确认
                </header>
                <p>
                  AI
                  可读取已添加的文件和选定的工作区。文件变更先生成差异提议，确认后应用。
                </p>
                <p className="ai-permission-status">
                  <Check />
                  当前已启用修改前确认
                </p>
              </section>
            </AIFloatingMenu>
          )}
        </div>
      }
      <p className="ai-chat-notice">{length}/4000 字</p>
    </div>
  );
}
