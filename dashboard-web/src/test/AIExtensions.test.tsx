// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { createRef, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AIExtensionSkills } from "@/components/AIExtensionSkills";
import {
  AISelectedSkillAttachments,
  AISelectedSkills,
} from "@/components/AISelectedSkills";
import { V2VideoControls } from "@/components/V2VideoControls";
import type { AIExtensionSkill } from "@/lib/types";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ invoke }));
beforeEach(() => {
  document.documentElement.dataset.uiRelease = "2";
  invoke.mockReset();
});
afterEach(() => {
  cleanup();
  document.documentElement.dataset.uiRelease = "classic";
});
const skills: AIExtensionSkill[] = [
  {
    id: "builtin-course",
    name: "课程导航",
    description: "定位课程",
    source: "builtin",
    enabled: true,
    allow_scripts: false,
    files: ["SKILL.md"],
    entry: "SKILL.md",
    instructions: "原文",
  },
  {
    id: "s-demo",
    name: "外部阅读",
    description: "阅读说明",
    source: "user",
    enabled: true,
    allow_scripts: false,
    files: ["skill/SKILL.md", "README.md", "scripts/main.py"],
    entry: "skill/SKILL.md",
    instructions: "---\nname: 外部阅读\n---\n原技能全文",
  },
  {
    id: "s-disabled",
    name: "关闭的技能",
    description: "不可选择",
    source: "user",
    enabled: false,
    allow_scripts: false,
    files: [],
    entry: "SKILL.md",
    instructions: "已关闭",
  },
];
it("内外标签控制导入位置，启用采用开关并写回接口", async () => {
  const onChanged = vi.fn();
  invoke.mockResolvedValue({ items: [], skills });
  render(<AIExtensionSkills skills={skills} onChanged={onChanged} />);
  expect(screen.queryByRole("button", { name: "导入 ZIP" })).toBeNull();
  fireEvent.click(screen.getByRole("switch", { name: "启用技能 课程导航" }));
  await waitFor(() =>
    expect(invoke).toHaveBeenCalledWith("ai_skill_update", {
      skill_id: "builtin-course",
      enabled: false,
    }),
  );
  fireEvent.click(screen.getByRole("tab", { name: "外部技能" }));
  fireEvent.click(screen.getByRole("button", { name: "导入 ZIP" }));
  await waitFor(() =>
    expect(invoke).toHaveBeenCalledWith("ai_skill_import", {}),
  );
  expect(screen.queryByRole("checkbox", { name: "外部阅读" })).toBeNull();
});
it("外部技能按原路径展示文件和指令，脚本权限默认关闭", async () => {
  render(<AIExtensionSkills skills={skills} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("tab", { name: "外部技能" }));
  fireEvent.click(screen.getByRole("button", { name: "外部阅读" }));
  expect(screen.getByLabelText("技能文件内容").textContent).toContain(
    "---\nname: 外部阅读",
  );
  expect(screen.getByRole("button", { name: "scripts/main.py" })).toBeTruthy();
  expect(
    (
      screen.getByRole("switch", {
        name: "允许 外部阅读 运行脚本",
      }) as HTMLInputElement
    ).checked,
  ).toBe(false);
});
it("技能详情在独立浮层配置，关闭恢复列表和触发焦点", async () => {
  render(<AIExtensionSkills skills={skills} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("tab", { name: "外部技能" }));
  const trigger = screen.getByRole("button", { name: "外部阅读" });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "外部阅读 配置" });
  expect(dialog.parentElement?.parentElement).toBe(document.body);
  expect(within(dialog).getByLabelText("技能文件内容").textContent).toContain(
    "原技能全文",
  );
  expect(
    document.querySelector(".extension-library")?.hasAttribute("inert"),
  ).toBe(false);
  await waitFor(() =>
    expect(document.activeElement?.getAttribute("aria-label")).toBe(
      "关闭配置窗口",
    ),
  );
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "关闭配置窗口" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("技能支持多选附件，已关闭的技能不出现，输入文本不受更改", () => {
  function Harness() {
    const [selected, setSelected] = useState<string[]>([]);
    return (
      <>
        <AISelectedSkillAttachments
          skills={skills}
          selected={selected}
          onChange={setSelected}
        />
        <textarea aria-label="问题" defaultValue="保留我的文字" />
        <AISelectedSkills
          skills={skills}
          selected={selected}
          onChange={setSelected}
        />
      </>
    );
  }
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "选择技能" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "课程导航" }));
  fireEvent.click(screen.getByRole("tab", { name: "外部" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "外部阅读" }));
  expect(screen.queryByText("关闭的技能")).toBeNull();
  expect(screen.getByLabelText("已附加技能").children.length).toBe(2);
  expect((screen.getByLabelText("问题") as HTMLTextAreaElement).value).toBe(
    "保留我的文字",
  );
  fireEvent.click(screen.getByRole("button", { name: "移除技能 课程导航" }));
  expect(screen.getByLabelText("已附加技能").children.length).toBe(1);
});
it("播放进度、播放暂停、跳转和音量使用实际媒体状态", async () => {
  const ref = createRef<HTMLVideoElement>();
  const seek = vi.fn();
  const toggle = vi.fn();
  render(
    <>
      <video ref={ref} />
      <V2VideoControls
        mediaRef={ref}
        source="fixture.mp4"
        fullscreen={false}
        onFullscreen={vi.fn()}
        onSeek={seek}
        onToggle={toggle}
        speedControl={<button type="button">1倍速</button>}
      />
    </>,
  );
  const video = ref.current!;
  Object.defineProperties(video, {
    duration: { configurable: true, value: 300 },
    currentTime: { configurable: true, value: 40 },
    paused: { configurable: true, value: false },
  });
  fireEvent.loadedMetadata(video);
  fireEvent.click(screen.getByRole("button", { name: "暂停视频" }));
  expect(toggle).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "前进10秒" }));
  expect(seek).toHaveBeenLastCalledWith(50);
  fireEvent.change(screen.getByRole("slider", { name: "视频播放进度" }), {
    target: { value: "180" },
  });
  expect(seek).toHaveBeenLastCalledWith(180);
  fireEvent.click(screen.getByRole("button", { name: "静音" }));
  expect(video.muted).toBe(true);
});
