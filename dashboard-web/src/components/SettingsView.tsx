import { useCallback, useEffect, useState } from "react";
import { ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import {
  getSettings,
  importAiConnection,
  organizeArchive,
  pickArchiveRoot,
  testAiConnection,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";

const AI_MODELS = [
  "deepseek-chat",
  "deepseek-reasoner",
  "minimax",
  "minimax-m2.7",
  "qwen",
  "qwen3.8-27b",
] as const;

export function SettingsView({
  onArchiveChanged,
}: {
  onArchiveChanged?: () => void;
}) {
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [mailAccount, setMailAccount] = useState("");
  const [aiBaseUrl, setAiBaseUrl] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [connectionJson, setConnectionJson] = useState("");
  const { showToast } = useToast();

  const applyStatus = (next: SettingsStatus) => {
    setStatus(next);
    setMailAccount(next.mail_account);
    setAiBaseUrl(next.ai_base_url);
    setAiModel(next.ai_model);
  };

  const load = useCallback(async () => {
    setError("");
    try {
      applyStatus(await getSettings());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "设置状态读取失败");
    }
  }, []);

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
      >
    >,
  ) => {
    const toastId = "settings-update";
    setBusy("update");
    showToast({
      id: toastId,
      kind: "info",
      message: "正在保存设置…",
      duration: 0,
    });
    try {
      applyStatus(await updateSettings(changes));
      showToast({ id: toastId, kind: "success", message: "设置已保存。" });
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: reason instanceof Error ? reason.message : "设置保存失败",
      });
    } finally {
      setBusy("");
    }
  };

  const importConnection = async () => {
    const toastId = "settings-ai-import";
    setBusy("ai-import");
    showToast({
      id: toastId,
      kind: "info",
      message: "正在保存 AI 连接…",
      duration: 0,
    });
    try {
      applyStatus(await importAiConnection(connectionJson));
      setConnectionJson("");
      showToast({
        id: toastId,
        kind: "success",
        message: "AI 连接已保存；API key 仅存入 macOS Keychain。",
      });
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: reason instanceof Error ? reason.message : "AI 连接保存失败",
      });
    } finally {
      setBusy("");
    }
  };

  const testConnection = async () => {
    const toastId = "settings-ai-test";
    setBusy("ai-test");
    showToast({
      id: toastId,
      kind: "info",
      message: "正在测试 AI 连接…",
      duration: 0,
    });
    try {
      const result = await testAiConnection();
      showToast({
        id: toastId,
        kind: "success",
        message: `AI 连接测试成功：${result.model} 返回 ${result.category}。`,
      });
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: reason instanceof Error ? reason.message : "AI 连接测试失败",
      });
    } finally {
      setBusy("");
    }
  };

  const chooseFolder = async () => {
    const toastId = "settings-pick-folder";
    setBusy("pick");
    try {
      const result = await pickArchiveRoot();
      applyStatus(result.settings);
      showToast({
        id: toastId,
        kind: result.cancelled ? "info" : "success",
        message: result.cancelled
          ? "已取消选择，归档目录未更改。"
          : "归档目录已更新。",
      });
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: reason instanceof Error ? reason.message : "目录选择失败",
      });
    } finally {
      setBusy("");
    }
  };

  const organize = async () => {
    const toastId = "settings-organize";
    setBusy("organize");
    showToast({
      id: toastId,
      kind: "info",
      message: "正在执行 AI 归档分类与整理…",
      duration: 0,
    });
    try {
      const result = await organizeArchive();
      showToast({
        id: toastId,
        kind: result.failed > 0 ? "info" : "success",
        message: `AI 归档完成：新分类 ${result.classified}，复用 ${result.reused}，规则回退 ${result.fallback}；移动 ${result.moved}，无需移动 ${result.unchanged}，失败 ${result.failed}。`,
      });
      onArchiveChanged?.();
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: reason instanceof Error ? reason.message : "整理失败",
      });
    } finally {
      setBusy("");
    }
  };

  if (!status && error)
    return <ErrorState message={error} retry={() => void load()} />;
  if (!status) return <LoadingState label="正在读取归档设置…" />;

  return (
    <div className="section-stack settings-page">
      <div className="view-intro">
        <div>
          <h2>归档与同步偏好</h2>
          <p>配置当前学期资料的自动归档、AI 分类与目录整理方式。</p>
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
        <div className="settings-row settings-account-row">
          <div>
            <label htmlFor="mail-account">
              <strong>邮箱账号</strong>
            </label>
            <small>
              {status.mail_account
                ? "同步将同时运行 Canvas 与邮箱。"
                : "未设置邮箱账号；当前同步仅运行 Canvas。"}
              密码不会保存在设置中，仅在同步时按需从 macOS Keychain 读取。
            </small>
          </div>
          <div className="settings-account-controls">
            <input
              id="mail-account"
              type="text"
              autoComplete="username"
              value={mailAccount}
              disabled={Boolean(busy)}
              onChange={(event) => setMailAccount(event.target.value)}
              placeholder="邮箱地址或账号标识"
            />
            <Button
              variant="outline"
              disabled={Boolean(busy)}
              onClick={() => void update({ mail_account: mailAccount.trim() })}
            >
              {busy === "update" ? "保存中…" : "保存邮箱账号"}
            </Button>
          </div>
        </div>
        <ToggleRow
          label="自动下载本学期资料"
          description="每次 Canvas 同步后自动下载最近 active 课程；历史学期仍需单文件下载。"
          checked={status.auto_download_current_term}
          disabled={Boolean(busy)}
          onChange={(checked) =>
            void update({ auto_download_current_term: checked })
          }
        />
        <ToggleRow
          label="按类别整理"
          description="路径中增加课程作业、课件、补充资料或其他分类层级。"
          checked={status.organize_by_category}
          disabled={Boolean(busy)}
          onChange={(checked) => void update({ organize_by_category: checked })}
        />
      </div>

      <section className="ai-settings" aria-labelledby="ai-settings-title">
        <div>
          <h3 id="ai-settings-title">AI 增量归档分类</h3>
          <p>
            AI 只处理课程名、文件名、Canvas
            文件夹及模块元数据，不会上传文件正文。
          </p>
        </div>
        <ToggleRow
          label="启用 AI 归档分类"
          description={
            status.ai_key_saved
              ? "API key 已保存在 macOS Keychain。"
              : "尚未保存 API key；未配置时会安全回退规则分类。"
          }
          checked={status.ai_enabled}
          disabled={Boolean(busy)}
          onChange={(checked) => void update({ ai_enabled: checked })}
        />
        <div className="ai-settings-grid">
          <label htmlFor="ai-base-url">
            <span>Base URL</span>
            <input
              id="ai-base-url"
              value={aiBaseUrl}
              disabled={Boolean(busy)}
              onChange={(event) => setAiBaseUrl(event.target.value)}
            />
          </label>
          <label htmlFor="ai-model">
            <span>模型</span>
            <select
              id="ai-model"
              value={aiModel}
              disabled={Boolean(busy)}
              onChange={(event) => setAiModel(event.target.value)}
            >
              {AI_MODELS.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="outline"
            disabled={Boolean(busy)}
            onClick={() =>
              void update({
                ai_base_url: aiBaseUrl.trim(),
                ai_model: aiModel,
              })
            }
          >
            保存 AI 参数
          </Button>
        </div>
        <label className="ai-json-field" htmlFor="ai-connection-json">
          <span>粘贴连接配置 JSON</span>
          <small>
            首次 macOS 授权请选择“始终允许”；本次运行后不再重复询问。
          </small>
          <textarea
            id="ai-connection-json"
            aria-label="粘贴连接配置 JSON"
            value={connectionJson}
            disabled={Boolean(busy)}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setConnectionJson(event.target.value)}
            placeholder={
              '{"_type":"newapi_channel_conn","url":"https://…/api/v1","key":"…","model":"deepseek-chat"}'
            }
          />
        </label>
        <div className="ai-settings-actions">
          <Button
            variant="outline"
            disabled={Boolean(busy) || !connectionJson.trim()}
            onClick={() => void importConnection()}
          >
            {busy === "ai-import" ? "保存中…" : "保存连接配置"}
          </Button>
          <Button
            variant="outline"
            disabled={Boolean(busy) || !status.ai_key_saved}
            onClick={() => void testConnection()}
          >
            {busy === "ai-test" ? "测试中…" : "测试 AI 归档连接"}
          </Button>
        </div>
      </section>

      <div className="settings-actions">
        <Button
          disabled={Boolean(busy) || !status.organize_by_category}
          onClick={() => void organize()}
        >
          {busy === "organize" ? "正在分类整理…" : "AI 归档分类/整理"}
        </Button>
        <span>仅处理最近同步的 Canvas active 课程，不触碰历史课程。</span>
      </div>
    </div>
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
