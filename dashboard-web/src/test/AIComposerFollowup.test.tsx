// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { type ComponentProps, createRef, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AIChatComposer } from "@/components/AIChatComposer";

beforeEach(() => {
  document.documentElement.dataset.uiRelease = "2";
});
afterEach(() => {
  cleanup();
  document.documentElement.dataset.uiRelease = "classic";
});
const base: ComponentProps<typeof AIChatComposer> = {
  draft: "",
  error: null,
  attachmentBusy: false,
  attachments: [],
  active: true,
  presets: [
    {
      id: "general",
      name: "综合助理",
      description: "按需调用工具",
      allowed_tools: [],
      is_default: true,
    },
  ],
  selectedPresetId: "general",
  model: "auto",
  depth: "standard",
  models: [
    { value: "auto", label: "自动" },
    { value: "deepseek-chat", label: "DeepSeek Flash" },
  ],
  depths: [{ value: "standard", label: "标准" }],
  hasMessages: true,
  sendShortcut: "enter",
  textareaRef: createRef(),
  onDraftChange: vi.fn(),
  onSelectPreset: vi.fn(),
  onModelChange: vi.fn(),
  onDepthChange: vi.fn(),
  onPickAttachment: vi.fn(),
  onRemoveAttachment: vi.fn(),
  onSubmit: vi.fn(),
};
it("运行时只有暂停；输入补充后变成可用发送，提交仍走补充指令回调", () => {
  const submit = vi.fn(),
    cancel = vi.fn();
  function Composer() {
    const [draft, setDraft] = useState("");
    return (
      <AIChatComposer
        {...base}
        busy
        draft={draft}
        onDraftChange={setDraft}
        onSubmit={submit}
        onCancel={cancel}
      />
    );
  }
  render(<Composer />);
  expect(screen.queryByRole("button", { name: "补充指令" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "停止本次任务" }));
  expect(cancel).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("输入问题"), {
    target: { value: "先读取讲义再总结" },
  });
  expect(screen.queryByRole("button", { name: "停止本次任务" })).toBeNull();
  const send = screen.getByRole("button", { name: "补充指令" });
  expect(send.hasAttribute("disabled")).toBe(false);
  fireEvent.click(send);
  expect(submit).toHaveBeenCalledWith("先读取讲义再总结");
});
it("工作区名就地展开目录、文件、模型配置，修改前确认不会跳MCP", () => {
  const page = vi.fn(),
    change = vi.fn(),
    model = vi.fn(),
    attachment = vi.fn();
  render(
    <AIChatComposer
      {...base}
      workspace={{
        available: true,
        configured: true,
        name: "Trial",
        path: null,
        reason: null,
      }}
      onOpenResources={page}
      onChangeWorkspace={change}
      onModelChange={model}
      onPickAttachment={attachment}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Trial" }));
  const config = screen.getByRole("dialog", { name: "当前对话配置" });
  expect(config.closest("[data-ai-floating-menu]")).toBeTruthy();
  fireEvent.click(within(config).getByRole("button", { name: "更换工作区" }));
  expect(change).toHaveBeenCalledWith("pick");
  fireEvent.click(
    within(config).getByRole("button", { name: "DeepSeek Flash" }),
  );
  expect(model).toHaveBeenCalledWith("deepseek-chat");
  fireEvent.click(within(config).getByRole("button", { name: "添加本地文件" }));
  expect(attachment).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "修改前确认" }));
  expect(screen.getByRole("dialog", { name: "文件操作权限" })).toBeTruthy();
  expect(page).not.toHaveBeenCalled();
});
