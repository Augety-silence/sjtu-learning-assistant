import {
  BookOpen,
  Bot,
  Cloud,
  Database,
  ExternalLink,
  Mail,
  Monitor,
  Moon,
  Pencil,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react";
import { AnimatePresence, motion, useIsPresent } from "motion/react";
import {
  type FormEvent,
  type RefObject,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import {
  deleteCredential,
  getSettings,
  openExternal,
  organizeArchive,
  pickArchiveRoot,
  saveCredential,
  testAiConnection,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus, ThemeMode } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

const AI_MODELS = [
  "deepseek-chat",
  "deepseek-reasoner",
  "minimax",
  "minimax-m2.7",
  "qwen",
  "qwen3.8-27b",
] as const;
const CONFIGURATION_GUIDE_URL =
  "https://bytedance.larkoffice.com/wiki/Iti5wHCN2iJ2PwksWoqcZjORn5f";
type ConfigKind = "canvas" | "mail" | "cloud" | "ai";

const themeOptions = [
  {
    mode: "system",
    label: "跟随系统",
    description: "自动匹配 macOS 外观",
    icon: Monitor,
  },
  { mode: "light", label: "浅色", description: "始终使用浅色外观", icon: Sun },
  { mode: "dark", label: "深色", description: "始终使用深色外观", icon: Moon },
] as const;

const configMeta = {
  canvas: {
    title: "Canvas",
    description: "课程、公告、作业与资料同步",
    secretLabel: "Canvas Access Token",
    placeholder: "粘贴 Canvas Access Token",
    icon: Database,
  },
  mail: {
    title: "交大邮箱",
    description: "邮件正文、内嵌图片与附件同步",
    secretLabel: "邮箱密码",
    placeholder: "输入邮箱密码",
    icon: Mail,
  },
  cloud: {
    title: "交大云盘",
    description: "Canvas 文件与邮件附件云端归档",
    secretLabel: "UserToken",
    placeholder: "粘贴交大云盘 UserToken",
    icon: Cloud,
  },
  ai: {
    title: "AI 模型",
    description: "AI Chat 与资料自动分类",
    secretLabel: "API Key",
    placeholder: "粘贴 API Key",
    icon: Bot,
  },
} as const;

export function SettingsView({
  onArchiveChanged,
  onThemeModeChange,
}: {
  onArchiveChanged?: () => void;
  onThemeModeChange?: (mode: ThemeMode) => void;
}) {
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<ConfigKind | null>(null);
  const [mailAccount, setMailAccount] = useState("");
  const [aiBaseUrl, setAiBaseUrl] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [secret, setSecret] = useState("");
  const configTriggerRef = useRef<HTMLButtonElement>(null);
  const { showToast } = useToast();

  const applyStatus = useCallback(
    (next: SettingsStatus) => {
      setStatus(next);
      setMailAccount(next.mail_account);
      setAiBaseUrl(next.ai_base_url);
      setAiModel(next.ai_model);
      onThemeModeChange?.(next.theme_mode);
    },
    [onThemeModeChange],
  );

  const load = useCallback(async () => {
    setError("");
    try {
      applyStatus(await getSettings());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "设置状态读取失败");
    }
  }, [applyStatus]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = async (
    changes: Partial<
      Pick<
        SettingsStatus,
        | "auto_download_current_term"
        | "organize_by_category"
        | "mail_account"
        | "ai_enabled"
        | "ai_base_url"
        | "ai_model"
        | "theme_mode"
      >
    >,
  ) => {
    setBusy("update");
    try {
      applyStatus(await updateSettings(changes));
      showToast({
        id: "settings-update",
        kind: "success",
        message: "设置已保存。",
      });
    } catch (reason) {
      showToast({
        id: "settings-update",
        kind: "error",
        message: reason instanceof Error ? reason.message : "设置保存失败",
      });
    } finally {
      setBusy("");
    }
  };

  const saveConfig = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    setBusy(`save-${editing}`);
    try {
      let next = status as SettingsStatus;
      if (editing === "mail") {
        next = await updateSettings({ mail_account: mailAccount.trim() });
        if (secret.trim()) {
          next = await saveCredential("mail", secret, mailAccount.trim());
        }
      } else if (editing === "ai") {
        next = await updateSettings({
          ai_base_url: aiBaseUrl.trim(),
          ai_model: aiModel,
        });
        if (secret.trim()) next = await saveCredential("ai", secret);
      } else if (secret.trim()) {
        next = await saveCredential(editing, secret);
      }
      applyStatus(next);
      setSecret("");
      setEditing(null);
      showToast({
        id: "settings-credential",
        kind: "success",
        message: `${configMeta[editing].title} 配置已保存。敏感信息仅存入 ${next.credential_storage_name}。`,
      });
    } catch (reason) {
      showToast({
        id: "settings-credential",
        kind: "error",
        message: reason instanceof Error ? reason.message : "配置保存失败",
      });
    } finally {
      setBusy("");
    }
  };

  const removeCredential = async () => {
    if (
      !editing ||
      !window.confirm(`确定删除${configMeta[editing].secretLabel}？`)
    )
      return;
    setBusy(`delete-${editing}`);
    try {
      applyStatus(
        await deleteCredential(
          editing,
          editing === "mail" ? mailAccount.trim() : "",
        ),
      );
      setSecret("");
      showToast({
        id: "settings-credential",
        kind: "success",
        message: "凭据已删除。",
      });
    } catch (reason) {
      showToast({
        id: "settings-credential",
        kind: "error",
        message: reason instanceof Error ? reason.message : "凭据删除失败",
      });
    } finally {
      setBusy("");
    }
  };

  const openConfigurationGuide = async () => {
    try {
      await openExternal(CONFIGURATION_GUIDE_URL);
    } catch (reason) {
      showToast({
        id: "settings-guide",
        kind: "error",
        message: reason instanceof Error ? reason.message : "配置指南打开失败",
      });
    }
  };

  const chooseFolder = async () => {
    setBusy("pick");
    try {
      const result = await pickArchiveRoot();
      applyStatus(result.settings);
      showToast({
        id: "settings-pick-folder",
        kind: result.cancelled ? "info" : "success",
        message: result.cancelled ? "已取消选择。" : "归档目录已更新。",
      });
    } catch (reason) {
      showToast({
        id: "settings-pick-folder",
        kind: "error",
        message: reason instanceof Error ? reason.message : "目录选择失败",
      });
    } finally {
      setBusy("");
    }
  };

  const organize = async () => {
    setBusy("organize");
    try {
      const result = await organizeArchive();
      showToast({
        id: "settings-organize",
        kind: result.failed > 0 ? "info" : "success",
        message: `AI 归档完成：新分类 ${result.classified}，复用 ${result.reused}，规则回退 ${result.fallback}；移动 ${result.moved}，无需移动 ${result.unchanged}，失败 ${result.failed}。`,
      });
      onArchiveChanged?.();
    } catch (reason) {
      showToast({
        id: "settings-organize",
        kind: "error",
        message: reason instanceof Error ? reason.message : "整理失败",
      });
    } finally {
      setBusy("");
    }
  };

  const testAI = async () => {
    setBusy("ai-test");
    try {
      const result = await testAiConnection();
      showToast({
        id: "settings-ai-test",
        kind: "success",
        message: `AI 连接测试成功：${result.model}。`,
      });
    } catch (reason) {
      showToast({
        id: "settings-ai-test",
        kind: "error",
        message: reason instanceof Error ? reason.message : "AI 连接测试失败",
      });
    } finally {
      setBusy("");
    }
  };

  if (!status && error) return <ErrorState message={error} retry={load} />;
  if (!status) return <LoadingState label="正在读取系统设置…" />;

  const savedByKind: Record<ConfigKind, boolean> = {
    canvas: status.canvas_token_saved,
    mail: status.mail_password_saved,
    cloud: status.cloud_token_saved,
    ai: status.ai_key_saved,
  };

  return (
    <div className="section-stack settings-page">
      <div className="view-intro">
        <div>
          <h2>系统设置</h2>
          <p>
            统一管理 Canvas、邮箱、交大云盘与 AI。凭据不会显示或写入设置文件。
          </p>
        </div>
      </div>

      {status.credential_status_error && (
        <p className="settings-warning" role="status">
          {status.credential_status_error}
        </p>
      )}

      <section className="settings-panel" aria-labelledby="appearance-title">
        <div className="section-header">
          <div>
            <h3 id="appearance-title">外观</h3>
            <p>选择界面主题；跟随系统会实时响应 macOS 外观变化。</p>
          </div>
        </div>
        <div
          className="theme-options"
          role="radiogroup"
          aria-labelledby="appearance-title"
        >
          {themeOptions.map(({ mode, label, description, icon: Icon }) => {
            const selected = status.theme_mode === mode;
            return (
              <label
                key={mode}
                className={
                  selected ? "theme-option is-selected" : "theme-option"
                }
              >
                <input
                  className="sr-only"
                  type="radio"
                  name="theme-mode"
                  value={mode}
                  checked={selected}
                  disabled={Boolean(busy)}
                  onChange={() => void update({ theme_mode: mode })}
                />
                <Icon aria-hidden="true" />
                <span>
                  <strong>{label}</strong>
                  <small>{description}</small>
                </span>
              </label>
            );
          })}
        </div>
      </section>

      <section
        className="configuration-section"
        aria-labelledby="configuration-title"
      >
        <div className="section-header">
          <div>
            <h3 id="configuration-title">连接配置</h3>
            <p>
              集中管理连接凭据；敏感信息只保存到 {status.credential_storage_name}。
            </p>
          </div>
          <button
            type="button"
            className="configuration-guide-link"
            aria-label="打开连接配置指南"
            onClick={() => void openConfigurationGuide()}
          >
            <BookOpen aria-hidden="true" />
            <span>配置指南</span>
            <ExternalLink aria-hidden="true" />
          </button>
        </div>
        <div className="configuration-grid">
          {(Object.keys(configMeta) as ConfigKind[]).map((kind) => {
            const meta = configMeta[kind];
            const Icon = meta.icon;
            return (
              <article className="configuration-card" key={kind}>
                <span className="configuration-icon">
                  <Icon aria-hidden="true" />
                </span>
                <div>
                  <h4>{meta.title}</h4>
                  <p>{meta.description}</p>
                  <span
                    className={
                      savedByKind[kind]
                        ? "config-status configured"
                        : "config-status"
                    }
                  >
                    {savedByKind[kind] ? "已配置" : "未配置"}
                  </span>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="configuration-edit-button"
                  onClick={(event) => {
                    configTriggerRef.current = event.currentTarget;
                    setSecret("");
                    setEditing(kind);
                  }}
                >
                  <Pencil aria-hidden="true" />
                  修改配置
                </Button>
              </article>
            );
          })}
        </div>
      </section>

      <section
        className="settings-panel"
        aria-labelledby="archive-settings-title"
      >
        <div className="section-header">
          <div>
            <h3 id="archive-settings-title">归档偏好</h3>
            <p>控制本地资料目录和自动整理行为。</p>
          </div>
        </div>
        <div className="settings-list">
          <div className="settings-row settings-path-row">
            <div>
              <strong>资料归档目录</strong>
              <span title={status.archive_root}>{status.archive_root}</span>
            </div>
            <Button
              variant="outline"
              disabled={Boolean(busy)}
              onClick={() => void chooseFolder()}
            >
              {busy === "pick" ? "选择中…" : "选择目录"}
            </Button>
          </div>
          <ToggleRow
            label="自动下载本学期资料"
            description="Canvas 同步后下载最近 active 课程资料。"
            checked={status.auto_download_current_term}
            disabled={Boolean(busy)}
            onChange={(checked) =>
              void update({ auto_download_current_term: checked })
            }
          />
          <ToggleRow
            label="按类别整理"
            description="按作业、课件和其他资料整理文件。"
            checked={status.organize_by_category}
            disabled={Boolean(busy)}
            onChange={(checked) =>
              void update({ organize_by_category: checked })
            }
          />
          <ToggleRow
            label="启用 AI 归档分类"
            description={
              status.ai_key_saved
                ? "使用已保存的 AI 配置辅助归档。"
                : "未配置时自动回退规则分类。"
            }
            checked={status.ai_enabled}
            disabled={Boolean(busy)}
            onChange={(checked) => void update({ ai_enabled: checked })}
          />
        </div>
        <div className="settings-actions">
          <Button
            disabled={Boolean(busy) || !status.organize_by_category}
            onClick={() => void organize()}
          >
            {busy === "organize" ? "正在分类整理…" : "AI 归档分类/整理"}
          </Button>
          <span>仅处理最近同步的 Canvas active 课程。</span>
        </div>
      </section>

      <AnimatePresence initial={false}>
        {editing && (
          <ConfigDialog
            kind={editing}
            saved={savedByKind[editing]}
            busy={Boolean(busy)}
            mailAccount={mailAccount}
            aiBaseUrl={aiBaseUrl}
            aiModel={aiModel}
            secret={secret}
            storageName={status.credential_storage_name}
            triggerRef={configTriggerRef}
            onMailAccount={setMailAccount}
            onAiBaseUrl={setAiBaseUrl}
            onAiModel={setAiModel}
            onSecret={setSecret}
            onClose={() => {
              if (!busy) setEditing(null);
            }}
            onSave={saveConfig}
            onDelete={() => void removeCredential()}
            onTestAI={() => void testAI()}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

function ConfigDialog({
  kind,
  saved,
  busy,
  mailAccount,
  aiBaseUrl,
  aiModel,
  secret,
  storageName,
  triggerRef,
  onMailAccount,
  onAiBaseUrl,
  onAiModel,
  onSecret,
  onClose,
  onSave,
  onDelete,
  onTestAI,
}: {
  kind: ConfigKind;
  saved: boolean;
  busy: boolean;
  mailAccount: string;
  aiBaseUrl: string;
  aiModel: string;
  secret: string;
  storageName: string;
  triggerRef: RefObject<HTMLButtonElement | null>;
  onMailAccount: (value: string) => void;
  onAiBaseUrl: (value: string) => void;
  onAiModel: (value: string) => void;
  onSecret: (value: string) => void;
  onClose: () => void;
  onSave: (event: FormEvent) => void;
  onDelete: () => void;
  onTestAI: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const isPresent = useIsPresent();
  const meta = configMeta[kind];
  const canSave =
    (kind === "mail" ? Boolean(mailAccount.trim()) : true) &&
    (kind === "ai" || kind === "mail" ? true : Boolean(secret.trim()));

  useModalFocus(dialogRef, onClose, {
    initialFocusRef: closeButtonRef,
    triggerRef,
    dismissible: !busy,
    active: isPresent,
  });

  return createPortal(
    <motion.div
      className="config-dialog-layer"
      data-modal-layer
      data-motion-layer="modal"
      role="presentation"
      aria-hidden={isPresent ? undefined : true}
      initial={{ opacity: 0.01 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: isPresent ? 0.14 : 0.12 }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <motion.section
        ref={dialogRef}
        className="config-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="config-dialog-title"
        aria-hidden={isPresent ? undefined : true}
        data-motion-surface="modal"
        tabIndex={-1}
        initial={{ opacity: 0.9, y: 6, scale: 0.99 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0.88, y: 4, scale: 0.99 }}
        transition={{
          duration: isPresent ? 0.18 : 0.14,
          ease: [0.16, 1, 0.3, 1],
        }}
      >
        <header>
          <div>
            <h3 id="config-dialog-title">{meta.title} 配置</h3>
            <p>{meta.description}</p>
          </div>
          <Button
            ref={closeButtonRef}
            variant="ghost"
            size="icon"
            aria-label="关闭配置窗口"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </Button>
        </header>
        <form onSubmit={onSave}>
          <div className="credential-note">
            <ShieldCheck aria-hidden="true" />
            <span>敏感信息只保存在 {storageName}。已保存的内容不会回显。</span>
          </div>
          {kind === "mail" && (
            <label>
              <span>邮箱账号</span>
              <input
                aria-label="邮箱账号"
                type="text"
                value={mailAccount}
                onChange={(event) => onMailAccount(event.target.value)}
                placeholder="name@sjtu.edu.cn"
                autoComplete="username"
              />
            </label>
          )}
          {kind === "ai" && (
            <>
              <label>
                <span>Base URL</span>
                <input
                  value={aiBaseUrl}
                  onChange={(event) => onAiBaseUrl(event.target.value)}
                />
              </label>
              <label>
                <span>默认模型</span>
                <select
                  value={aiModel}
                  onChange={(event) => onAiModel(event.target.value)}
                >
                  {AI_MODELS.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          <label>
            <span>
              {meta.secretLabel}
              {saved ? "（留空则保持不变）" : ""}
            </span>
            <input
              aria-label={meta.secretLabel}
              type="password"
              value={secret}
              onChange={(event) => onSecret(event.target.value)}
              placeholder={meta.placeholder}
              autoComplete="new-password"
            />
          </label>
          <footer>
            {saved && (
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={onDelete}
              >
                删除凭据
              </Button>
            )}
            {kind === "ai" && saved && (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={onTestAI}
              >
                测试连接
              </Button>
            )}
            <span />
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={onClose}
            >
              取消
            </Button>
            <Button type="submit" disabled={busy || !canSave}>
              {busy ? "保存中…" : "保存配置"}
            </Button>
          </footer>
        </form>
      </motion.section>
    </motion.div>,
    document.body,
  );
}

function ToggleRow({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="settings-row settings-toggle-row">
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}
