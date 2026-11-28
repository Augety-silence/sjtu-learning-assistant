// @vitest-environment jsdom

import { webcrypto } from "node:crypto";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@/lib/api";
import { V2Schedule } from "@/v2/Schedule";

vi.mock("@/lib/api", () => ({ invoke: vi.fn() }));
const now = new Date();
const event = {
  id: "system-1",
  calendar_id: "work",
  calendar_title: "个人",
  title: "Mac 原生安排",
  start_at: new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    10,
  ).toISOString(),
  end_at: new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    11,
  ).toISOString(),
  all_day: false,
  timezone: "Asia/Shanghai",
  location: "图书馆",
  notes: "",
  revision: "v1",
  writable: true,
  recurring: false,
  alarms: [-900],
};
let enabled = true;
function view() {
  return render(
    <V2Schedule
      events={[]}
      month={now}
      onMonthChange={vi.fn()}
      onImport={vi.fn()}
      importing={false}
      status=""
      onRetry={vi.fn()}
    />,
  );
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  localStorage.clear();
  enabled = true;
});
describe("Mac 日历双向连接", () => {
  it("Windows 仅显示本机日程与手动导出，不尝试 EventKit 或系统授权", async () => {
    vi.mocked(invoke).mockResolvedValue({
      supported: false,
      enabled: false,
      authorization: "unsupported",
      platform: "win32",
      fallback: "local_ics",
    });
    view();
    fireEvent.click(
      await screen.findByRole("button", { name: "配置 Windows 日程" }),
    );
    expect(
      screen
        .getByRole("dialog", { name: "Windows 日程配置" })
        .closest("[data-ai-floating-menu]"),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "导出 0 项本机日程" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.queryByText("Mac 日历")).toBeNull();
    expect(invoke).not.toHaveBeenCalledWith("native_calendar_connect");
    expect(invoke).not.toHaveBeenCalledWith("native_calendar_calendars");
  });
  function mock() {
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "native_calendar_status")
        return {
          supported: true,
          enabled,
          authorization: enabled ? "full_access" : "not_determined",
        };
      if (action === "native_calendar_calendars")
        return {
          items: [
            { id: "work", title: "个人", writable: true, source: "本机" },
          ],
        };
      if (action === "native_calendar_events")
        return { items: [event], truncated: false };
      if (action === "native_calendar_save")
        return { ...event, title: "编辑后安排", revision: "v2" };
      if (action === "native_calendar_connect") {
        enabled = true;
        return {};
      }
      return {};
    });
  }
  it("读取原生日历并在编辑时传递版本、提醒和时区", async () => {
    mock();
    view();
    const entries = await screen.findAllByText("Mac 原生安排");
    fireEvent.click(entries[0]);
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("标题"), {
      target: { value: "编辑后安排" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith(
        "native_calendar_save",
        expect.objectContaining({
          id: "system-1",
          revision: "v1",
          occurrence_start: event.start_at,
          title: "编辑后安排",
          alarms: [-900],
          span: "this",
        }),
      ),
    );
    expect(localStorage.getItem("sjtu-v2-personal-events")).toBeNull();
  });
  it("未连接时保留本机日程路径，点击连接才申请系统访问", async () => {
    enabled = false;
    mock();
    view();
    fireEvent.click(
      await screen.findByRole("button", { name: "配置 Mac 日历连接" }),
    );
    await screen.findByRole("button", { name: "连接 Mac 日历" });
    expect(invoke).not.toHaveBeenCalledWith("native_calendar_connect");
    fireEvent.click(screen.getByRole("button", { name: "连接 Mac 日历" }));
    await screen.findByText("双向连接");
    expect(invoke).toHaveBeenCalledWith("native_calendar_connect");
  });
  it("连接配置默认收起，点击状态在浮层展开，选择目标日历后仍在日程页", async () => {
    mock();
    const { container } = view();
    await screen.findByText("双向连接");
    expect(screen.queryByText("新日程保存到")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "配置 Mac 日历连接" }));
    const panel = screen.getByRole("dialog", { name: "Mac 日历配置" });
    expect(panel.closest("[data-ai-floating-menu]")).toBeTruthy();
    expect(
      container.querySelector(".v2-native-calendar")?.contains(panel),
    ).toBe(false);
    expect(screen.getByLabelText("新日程保存到")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Mac 日历配置" })).toBeNull();
    expect(document.querySelector(".v2-time-grid")).toBeTruthy();
  });
  it("权限错误可见并保留草稿", async () => {
    mock();
    view();
    await screen.findAllByText("Mac 原生安排");
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "native_calendar_save")
        throw new Error("日程已在其他位置修改");
      return {};
    });
    fireEvent.click(screen.getByRole("button", { name: "添加日程" }));
    fireEvent.change(screen.getByLabelText("标题"), {
      target: { value: "保留草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByRole("alert");
    expect((screen.getByLabelText("标题") as HTMLInputElement).value).toBe(
      "保留草稿",
    );
  });
  it("取消后重开保留全天、跨日和提醒，迁移本机全天日程不变为定时", async () => {
    const legacy = {
      id: "legacy-allday",
      personal: true,
      allDay: true,
      title: "旧全天",
      startAt: new Date(
        now.getFullYear(),
        now.getMonth(),
        now.getDate(),
      ).toISOString(),
      endAt: new Date(
        now.getFullYear(),
        now.getMonth(),
        now.getDate() + 1,
      ).toISOString(),
      courseName: "个人日程",
      source: "personal",
    };
    vi.stubGlobal("crypto", webcrypto);
    localStorage.setItem("sjtu-v2-personal-events", JSON.stringify([legacy]));
    mock();
    view();
    await screen.findAllByText("Mac 原生安排");
    fireEvent.click(screen.getByRole("button", { name: "添加日程" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /全天/ }));
    fireEvent.change(screen.getByLabelText("原生提醒"), {
      target: { value: "-1800" },
    });
    const end = (screen.getByLabelText("结束日期") as HTMLInputElement).value;
    const start = (screen.getByLabelText("日期") as HTMLInputElement).value;
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    // 标题仍为空时也要完整恢复；选择另一日期不能把旧结束日期和新开始日期混在一起。
    fireEvent.click(document.querySelector(".v2-month-grid button")!);
    fireEvent.click(screen.getByRole("button", { name: "添加日程" }));
    expect((screen.getByLabelText("日期") as HTMLInputElement).value).toBe(
      start,
    );
    expect(
      (screen.getByRole("checkbox", { name: /全天/ }) as HTMLInputElement)
        .checked,
    ).toBe(true);
    expect((screen.getByLabelText("结束日期") as HTMLInputElement).value).toBe(
      end,
    );
    expect((screen.getByLabelText("原生提醒") as HTMLSelectElement).value).toBe(
      "-1800",
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByRole("button", { name: "配置 Mac 日历连接" }));
    fireEvent.click(screen.getByRole("button", { name: "迁移 1 项本机日程" }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith(
        "native_calendar_save",
        expect.objectContaining({ title: "旧全天", all_day: true }),
      ),
    );
  });
  it("编辑旧本机日程后仅保留原生一份，存储失败重试复用迁移ID", async () => {
    vi.stubGlobal("crypto", webcrypto);
    const legacy = {
      id: "personal-legacy",
      personal: true,
      title: "旧本机安排",
      startAt: event.start_at,
      endAt: event.end_at,
      courseName: "个人日程",
      source: "personal",
      description: "",
      location: "",
    };
    localStorage.setItem("sjtu-v2-personal-events", JSON.stringify([legacy]));
    mock();
    view();
    await screen.findAllByText("Mac 原生安排");
    fireEvent.click(screen.getAllByText("旧本机安排")[0]);
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("标题"), {
      target: { value: "编辑后安排" },
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => {
      throw new Error("本机存储暂不可用");
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByRole("alert");
    expect(
      JSON.parse(localStorage.getItem("sjtu-v2-personal-events") || "[]"),
    ).toHaveLength(1);
    const first = vi
      .mocked(invoke)
      .mock.calls.find((call) => call[0] === "native_calendar_save")?.[1];
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(
        JSON.parse(localStorage.getItem("sjtu-v2-personal-events") || "[]"),
      ).toHaveLength(0),
    );
    const writes = vi
      .mocked(invoke)
      .mock.calls.filter((call) => call[0] === "native_calendar_save");
    expect(writes).toHaveLength(2);
    expect(writes[1][1]?.request_id).toBe(first?.request_id);
    expect(writes[1][1]?.id).toBeUndefined();
  });
});
