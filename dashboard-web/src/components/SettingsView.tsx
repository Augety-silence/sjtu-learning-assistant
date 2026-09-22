import { useCallback, useEffect, useState } from "react";
import { ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import {
  getSettings,
  organizeArchive,
  pickArchiveRoot,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";

export function SettingsView({
  onArchiveChanged,
}: {
  onArchiveChanged?: () => void;
}) {
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      setStatus(await getSettings());
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
        "auto_download_current_term" | "organize_by_category"
      >
    >,
  ) => {
    setBusy("update");
    setError("");
    setNotice("");
    try {
      const next = await updateSettings(changes);
      setStatus(next);
      setNotice("设置已保存。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "设置保存失败");
    } finally {
      setBusy("");
    }
  };

  const chooseFolder = async () => {
    setBusy("pick");
    setError("");
    setNotice("");
    try {
      const result = await pickArchiveRoot();
      setStatus(result.settings);
      setNotice(
        result.cancelled ? "已取消选择，归档目录未更改。" : "归档目录已更新。",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "目录选择失败");
    } finally {
      setBusy("");
    }
  };

  const organize = async () => {
    setBusy("organize");
    setError("");
    setNotice("");
    try {
      const result = await organizeArchive();
      setNotice(
        `整理完成：移动 ${result.moved}，无需移动 ${result.unchanged}，失败 ${result.failed}。`,
      );
      onArchiveChanged?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "整理失败");
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
          <p>配置当前学期资料的自动归档与目录整理方式。</p>
        </div>
      </div>
      {error && (
        <div className="settings-error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
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
      <div className="settings-actions">
        <Button
          disabled={Boolean(busy) || !status.organize_by_category}
          onClick={() => void organize()}
        >
          {busy === "organize" ? "正在整理…" : "立即整理现有文件"}
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
