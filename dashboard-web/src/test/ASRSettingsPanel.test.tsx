// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { ASRSettingsPanel } from "@/components/ASRSettingsPanel";
import {
  saveCredential,
  transcribeSelectedAudio,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  saveCredential: vi.fn(),
  deleteCredential: vi.fn(),
  transcribeSelectedAudio: vi.fn(),
  updateSettings: vi.fn(),
}));
const status = {
  asr_endpoint: "https://api.siliconflow.cn/v1/audio/transcriptions",
  asr_model: "Qwen/Qwen3-ASR-1.7B",
  asr_key_saved: true,
} as SettingsStatus;
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});
describe("独立语音设置", () => {
  it("只保存语音配置和独立密钥，不修改聊天模型", async () => {
    vi.mocked(updateSettings).mockResolvedValue(status);
    vi.mocked(saveCredential).mockResolvedValue(status);
    render(<ASRSettingsPanel status={status} onChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("语音识别 API Key"), {
      target: { value: "test-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存语音配置" }));
    await waitFor(() =>
      expect(saveCredential).toHaveBeenCalledWith("asr", "test-secret"),
    );
    expect(updateSettings).toHaveBeenCalledWith({
      asr_endpoint: status.asr_endpoint,
      asr_model: status.asr_model,
    });
    expect(
      (screen.getByLabelText("语音识别 API Key") as HTMLInputElement).value,
    ).toBe("");
  });
  it("确认后选择音频，结果可任意选择复制", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(transcribeSelectedAudio).mockResolvedValue({
      text: "课堂测试内容",
    });
    render(<ASRSettingsPanel status={status} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "选择音频并转写" }));
    expect(
      await screen.findByLabelText("语音识别结果，可选择复制"),
    ).toHaveProperty("value", "课堂测试内容");
  });
  it("取消外发授权不调用识别接口", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ASRSettingsPanel status={status} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "选择音频并转写" }));
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "选择音频并转写" })
          .hasAttribute("disabled"),
      ).toBe(false),
    );
    expect(transcribeSelectedAudio).not.toHaveBeenCalled();
  });
  it("更改模型未保存时不可转写，不静默回退模型", () => {
    render(<ASRSettingsPanel status={status} onChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("语音识别模型"), {
      target: { value: "FunAudioLLM/SenseVoiceSmall" },
    });
    expect(
      screen
        .getByRole("button", { name: "选择音频并转写" })
        .hasAttribute("disabled"),
    ).toBe(true);
  });
});
