import { useCallback, useEffect, useState } from "react";
import { ErrorState, LoadingState } from "@/components/States";
import { invoke } from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";

const labels: Record<string, string> = {
  canvas_token: "Canvas Access Token",
  mail_account: "邮箱账号（SJTU_EMAIL）",
  mail_password: "邮箱密码（macOS Keychain）",
};

export function SettingsView() {
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      setStatus(await invoke<SettingsStatus>("settings_status"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "设置状态读取失败");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  if (error) return <ErrorState message={error} retry={() => void load()} />;
  if (!status) return <LoadingState label="正在检查本机设置…" />;
  return (
    <div className="section-stack settings-page">
      <div className="view-intro">
        <div>
          <h2>本机设置</h2>
          <p>凭据仅从 macOS Keychain 读取，不会传给前端。</p>
        </div>
      </div>
      {status.missing.length > 0 && (
        <div className="settings-missing" role="alert">
          <h3>需要完成设置</h3>
          <p>缺少：{status.missing.map((item) => labels[item]).join("、")}。</p>
          <p>请在终端运行现有 Canvas / 邮箱配置流程，随后重新检查。</p>
        </div>
      )}
      <div className="settings-list">
        <StatusRow label="macOS Keychain" ready={status.keychain_available} />
        <StatusRow label="Canvas Token" ready={status.canvas_configured} />
        <StatusRow label="邮箱账号" ready={status.mail_account_configured} />
        <StatusRow label="邮箱密码" ready={status.mail_password_configured} />
        <StatusRow label="资料归档目录" ready={status.archive_root_ready} />
      </div>
    </div>
  );
}

function StatusRow({ label, ready }: { label: string; ready: boolean }) {
  return (
    <div className="settings-row">
      <span>{label}</span>
      <span
        className={`status-tag ${ready ? "status-downloaded" : "status-failed"}`}
      >
        {ready ? "已就绪" : "缺失"}
      </span>
    </div>
  );
}
