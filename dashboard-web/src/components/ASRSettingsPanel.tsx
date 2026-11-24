import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
import { SecretInput } from "@/components/ui/SecretInput";
import {
  deleteCredential,
  openExternal,
  saveCredential,
  transcribeSelectedAudio,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";
import "./asr-settings.css";
import { RecordingASRPanel } from "./RecordingASRPanel";

export function ASRSettingsPanel({
  status,
  onChange,
  onBusyChange,
  active = true,
  discardDraft = false,
}: {
  status: SettingsStatus;
  onChange: (status: SettingsStatus) => void;
  onBusyChange?: (busy: boolean) => void;
  active?: boolean;
  discardDraft?: boolean;
}) {
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  useEffect(() => {
    if (discardDraft) {
      setSecret("");
      setText("");
      setError("");
    }
  }, [discardDraft]);
  useEffect(() => {
    onBusyChange?.(Boolean(busy));
  }, [busy, onBusyChange]);
  useEffect(() => {
    setEndpoint(
      status.asr_endpoint ??
        "https://api.siliconflow.cn/v1/audio/transcriptions",
    );
    setModel(status.asr_model ?? "XingChenAGI/XingChenASR-V3.2-Ultra");
  }, [status.asr_endpoint, status.asr_model]);
  const run = async (action: string, task: () => Promise<void>) => {
    setBusy(action);
    setError("");
    try {
      await task();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "语音识别操作失败");
    } finally {
      setBusy("");
    }
  };
  return (
    <section className="asr-settings-panel" aria-labelledby="asr-title">
      <div className="section-header">
        <div className="heading-with-info">
          <h4 id="asr-title">硅基流动</h4>
          <InfoTooltip label="语音接口说明">
            独立于交大聊天模型，只需自己的语音 API Key。全部未完成录播仅在下方
            明确授权后外发处理，可暂停、续传和有界手动重试。费用以账户权限为准，
            不自动切换模型。完整原文 MD 标记为人工未校对，不伪造字幕时间轴。
          </InfoTooltip>
        </div>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run("save", async () => {
            const next = await updateSettings({
              asr_endpoint: endpoint.trim(),
              asr_model: model.trim(),
            });
            onChange(next);
            if (secret.trim()) {
              onChange(await saveCredential("asr", secret));
              setSecret("");
            }
          });
        }}
      >
        <fieldset
          disabled={Boolean(busy)}
          style={{ border: 0, padding: 0, margin: 0 }}
        >
          <div className="settings-list">
            <label className="settings-row">
              <strong>接口地址</strong>
              <input
                aria-label="语音识别接口地址"
                type="url"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                required
                style={{ width: "min(100%, 460px)", minWidth: 0 }}
              />
            </label>
            <label className="settings-row">
              <strong>识别模型</strong>
              <input
                aria-label="语音识别模型"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                list="asr-models"
                required
                style={{ width: "min(100%, 460px)", minWidth: 0 }}
              />
            </label>
            <datalist id="asr-models">
              <option value="XingChenAGI/XingChenASR-V3.2-Ultra" />
              <option value="Qwen/Qwen3-ASR-1.7B" />
              <option value="FunAudioLLM/SenseVoiceSmall" />
              <option value="TeleAI/TeleSpeechASR" />
            </datalist>
            <SecretInput
              label="语音识别 API Key"
              hint={
                status.asr_key_saved
                  ? "语音 API Key（留空保持不变）"
                  : "语音 API Key"
              }
              value={secret}
              onChange={setSecret}
              placeholder="粘贴硅基流动 API Key"
              autoComplete="new-password"
              active={!busy && active}
              required={false}
            />
          </div>
          <div className="settings-actions">
            <Button type="submit" loading={busy === "save"}>
              保存语音配置
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={
                !status.asr_key_saved ||
                Boolean(secret) ||
                endpoint.trim() !== status.asr_endpoint ||
                model.trim() !== status.asr_model
              }
              loading={busy === "transcribe"}
              onClick={() =>
                void run("transcribe", async () => {
                  if (
                    !window.confirm(
                      "所选音频将发送到已保存的语音识别接口，可能产生费用。继续选择？",
                    )
                  )
                    return;
                  setText("");
                  const result = await transcribeSelectedAudio();
                  if (!result.cancelled) setText(result.text ?? "");
                })
              }
            >
              选择音频并转写
            </Button>
            {status.asr_key_saved && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  if (
                    window.confirm(
                      "删除语音识别密钥后将无法转写音频，交大聊天模型不受影响。继续？",
                    )
                  )
                    void run("delete", async () => {
                      onChange(await deleteCredential("asr"));
                      setSecret("");
                    });
                }}
              >
                删除语音密钥
              </Button>
            )}
          </div>
        </fieldset>
      </form>
      <RecordingASRPanel
        settings={status}
        active={active}
        disabled={
          Boolean(busy) ||
          Boolean(secret) ||
          endpoint.trim() !==
            (status.asr_endpoint ??
              "https://api.siliconflow.cn/v1/audio/transcriptions") ||
          model.trim() !==
            (status.asr_model ?? "XingChenAGI/XingChenASR-V3.2-Ultra")
        }
      />
      {error && <p role="alert">{error}</p>}
      {error.includes("HTTP 402") && (
        <Button
          type="button"
          variant="outline"
          onClick={() =>
            void openExternal("https://cloud.siliconflow.cn/").catch((reason) =>
              setError(
                reason instanceof Error ? reason.message : "控制台打开失败",
              ),
            )
          }
        >
          打开硅基流动控制台
        </Button>
      )}
      {text && (
        <textarea
          aria-label="语音识别结果，可选择复制"
          readOnly
          value={text}
          rows={6}
          style={{
            width: "100%",
            maxHeight: "40vh",
            resize: "vertical",
            userSelect: "text",
          }}
        />
      )}
    </section>
  );
}
