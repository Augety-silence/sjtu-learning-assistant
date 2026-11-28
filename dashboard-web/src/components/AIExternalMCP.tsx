import {
  Cable,
  Check,
  FileJson,
  Plus,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { ExtensionSwitch } from "@/components/AIExtensionSkills";
import { ExtensionConfigDialog } from "@/components/ExtensionConfigDialog";
import { invoke } from "@/lib/api";
import type { ExternalMCPService } from "@/lib/types";

const DDG = JSON.stringify(
  {
    mcpServers: {
      DuckDuckGo: {
        command: "uvx",
        args: [
          "--from",
          "duckduckgo-mcp-server[browser]==0.7.0",
          "duckduckgo-mcp-server",
        ],
        env: { DDG_SEARCH_BACKEND: "auto", DDG_REF_URL_THRESHOLD: "0" },
      },
    },
  },
  null,
  2,
);

export function AIExternalMCP() {
  const [services, setServices] = useState<ExternalMCPService[]>([]);
  const [editing, setEditing] = useState(false);
  const [config, setConfig] = useState(DDG);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("Model Context Protocol");
  const [result, setResult] = useState("");
  const perform = async (
    action: string,
    payload: Record<string, unknown> = {},
  ) => {
    setBusy(true);
    setError("");
    try {
      const data = await invoke<{ items: ExternalMCPService[] }>(
        action,
        payload,
      );
      setServices(data.items);
      return true;
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "服务配置失败，请重试。",
      );
      return false;
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void perform("ai_mcp_list");
  }, []);
  const current = services.find((s) => s.id === selected);
  return (
    <div className="extension-library">
      <div className="extension-toolbar">
        <p>启用服务后，综合助手可按需调用；资料库助手不连接外部服务。</p>
        <button type="button" onClick={() => setEditing((v) => !v)}>
          <Plus size={15} />
          添加 / 导入
        </button>
      </div>
      {error && !current && !editing && (
        <p role="alert" className="extension-error">
          {error}
        </p>
      )}
      {editing && (
        <ExtensionConfigDialog
          title="添加 MCP 服务"
          onClose={() => setEditing(false)}
        >
          {error && (
            <p role="alert" className="extension-error">
              {error}
            </p>
          )}
          <section className="extension-import" aria-label="导入 MCP 服务">
            <header>
              <strong>标准 MCP JSON</strong>
              <label className="extension-file-pick">
                <FileJson size={15} />
                读取 JSON 文件
                <input
                  type="file"
                  accept=".json,application/json"
                  onChange={async (e) => {
                    const picked = e.target.files?.[0];
                    if (picked) {
                      if (picked.size > 64000) {
                        setError("配置文件不能超过64KB。");
                        return;
                      }
                      setConfig(await picked.text());
                    }
                  }}
                />
              </label>
            </header>
            <textarea
              aria-label="MCP JSON 配置"
              spellCheck={false}
              value={config}
              onChange={(e) => setConfig(e.target.value)}
            />
            <div className="extension-import-actions">
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    const data = await invoke<{ config: string }>(
                      "ai_mcp_template",
                      { provider: "bing" },
                    );
                    setConfig(data.config);
                  } catch (reason) {
                    setError(
                      reason instanceof Error
                        ? reason.message
                        : "无法读取搜索配置。",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Bing 搜索模板
              </button>
              <button type="button" onClick={() => setConfig(DDG)}>
                DuckDuckGo 模板
              </button>
              <button
                type="button"
                disabled={busy}
                className="extension-primary"
                onClick={async () => {
                  if (await perform("ai_mcp_import", { config }))
                    setEditing(false);
                }}
              >
                {busy ? "导入中…" : "导入配置"}
              </button>
            </div>
          </section>
        </ExtensionConfigDialog>
      )}
      {!services.length && !editing && (
        <div className="extension-empty">
          <Cable size={28} />
          <strong>连接你的第一个 MCP 服务</strong>
          <p>
            支持本地 stdio、Streamable HTTP 和 SSE，以及标准 mcpServers 配置。
          </p>
          <button type="button" onClick={() => setEditing(true)}>
            添加 DuckDuckGo
          </button>
        </div>
      )}
      <div className="extension-rows">
        {services.map((s) => (
          <div
            key={s.id}
            className={`extension-row ${selected === s.id ? "is-selected" : ""}`}
          >
            <Cable size={19} />
            <button
              type="button"
              className="extension-row-info"
              onClick={() => {
                setSelected(s.id);
                setResult("");
              }}
            >
              <strong>{s.name}</strong>
              <span>
                {s.transport} ·{" "}
                {s.tools.length ? `${s.tools.length} 个工具` : "尚未连接"} ·{" "}
                {s.enabled ? "已启用" : "已关闭"}
              </span>
            </button>
            {s.tools.length ? (
              <ExtensionSwitch
                checked={s.enabled}
                disabled={busy}
                label={`启用 MCP ${s.name}`}
                onChange={(enabled) =>
                  void perform("ai_mcp_update", { id: s.id, enabled })
                }
              />
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setSelected(s.id);
                  void perform("ai_mcp_connect", { id: s.id });
                }}
              >
                连接
              </button>
            )}
          </div>
        ))}
      </div>
      {current && (
        <ExtensionConfigDialog
          title={`${current.name} 服务配置`}
          onClose={() => {
            setSelected(null);
            setResult("");
            setError("");
          }}
          actions={
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void perform("ai_mcp_connect", { id: current.id })
                }
              >
                <RefreshCw size={14} />
                重新连接
              </button>
              <button
                type="button"
                aria-label={`删除服务 ${current.name}`}
                disabled={busy}
                onClick={() => {
                  void perform("ai_mcp_delete", { id: current.id });
                  setSelected(null);
                }}
              >
                <Trash2 size={15} />
              </button>
            </>
          }
        >
          {error && (
            <p role="alert" className="extension-error">
              {error}
            </p>
          )}
          <section className="extension-detail">
            <div className="extension-connection-info">
              <span>{current.command || current.url}</span>
              {current.env_keys.length > 0 && (
                <span>环境变量：{current.env_keys.join("、")}</span>
              )}
              <label>
                工具权限
                <select
                  aria-label="MCP 工具权限"
                  value={current.permission}
                  disabled={busy}
                  onChange={(e) =>
                    void perform("ai_mcp_update", {
                      id: current.id,
                      permission: e.target.value,
                    })
                  }
                >
                  <option value="read_only">仅声明只读的工具</option>
                  <option value="all">已勾选的全部工具</option>
                </select>
              </label>
            </div>
            <div className="extension-tools">
              {current.tools.map((t) => (
                <label key={t.name}>
                  <input
                    type="checkbox"
                    checked={t.enabled}
                    disabled={busy}
                    onChange={(e) =>
                      void perform("ai_mcp_update", {
                        id: current.id,
                        tools: current.tools
                          .filter((other) =>
                            other.name === t.name
                              ? e.target.checked
                              : other.enabled,
                          )
                          .map((other) => other.name),
                      })
                    }
                  />
                  <div>
                    <strong>{t.name}</strong>
                    <p>{t.description}</p>
                  </div>
                  <span>
                    {t.read_only
                      ? "只读"
                      : current.permission === "all"
                        ? "已授权"
                        : "需全部工具权限"}
                  </span>
                </label>
              ))}
            </div>
            {current.tools.some((t) => t.name === "search") && (
              <form
                className="extension-search-test"
                onSubmit={async (e) => {
                  e.preventDefault();
                  setBusy(true);
                  setError("");
                  try {
                    const data = await invoke<{
                      text: string;
                      isError: boolean;
                    }>("ai_mcp_test", { id: current.id, query });
                    setResult(data.text);
                    if (data.isError)
                      setError("搜索未完成，下面显示服务返回的原因。");
                  } catch (reason) {
                    setError(
                      reason instanceof Error
                        ? reason.message
                        : "搜索测试失败。",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <label>
                  <Search size={15} />
                  <input
                    aria-label="MCP 测试搜索词"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                <button type="submit" disabled={busy || !current.enabled}>
                  <Check size={14} />
                  {busy ? "测试中…" : "测试搜索"}
                </button>
              </form>
            )}
            {result && (
              <pre className="extension-test-result" aria-label="MCP 搜索结果">
                {result}
              </pre>
            )}
          </section>
        </ExtensionConfigDialog>
      )}
    </div>
  );
}
