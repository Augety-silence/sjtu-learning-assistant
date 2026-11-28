// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCalendar, getTimetableSchedule, invoke } from "@/lib/api";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@/test/render";
import { readPersonalEvents } from "@/v2/calendarData";
import { V2Overview } from "@/v2/Overview";
import { V2Dialog } from "@/v2/primitives";
import { V2Schedule } from "@/v2/Schedule";
import { V2SettingsSurface } from "@/v2/Settings";
import { V2Shell } from "@/v2/Shell";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getCalendar: vi.fn(),
  getTimetableSchedule: vi.fn(),
  invoke: vi.fn(),
}));
beforeEach(() => {
  document.documentElement.dataset.uiRelease = "2";
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  vi.mocked(getCalendar).mockResolvedValue({
    month_events: [],
    upcoming_events: [],
    context_codes: [],
    year: 2026,
    month: 11,
  } as never);
  vi.mocked(getTimetableSchedule).mockResolvedValue({ events: [] });
  vi.mocked(invoke).mockResolvedValue({
    courses: 9,
    upcoming_deadlines: 0,
    unread_emails: 2,
    deadlines: [],
    messages: [],
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
describe("2.0 全新页面与真实行为", () => {
  it("概览只有三项摘要，并具有三个独立内容列", async () => {
    const { container } = render(<V2Overview navigate={vi.fn()} />);
    await screen.findByText("未读邮件");
    expect(container.querySelectorAll(".v2-summary>button")).toHaveLength(3);
    expect(container.querySelector(".v2-overview-grid")?.children).toHaveLength(
      3,
    );
    expect(screen.queryByText("已同步课程")).toBeNull();
    expect(screen.queryByText(/下午好/)).toBeNull();
  });
  it("读取失败后可以重试，日程一个来源失败仍显示另一个来源", async () => {
    vi.mocked(invoke).mockRejectedValueOnce(new Error("同步索引暂不可用"));
    vi.mocked(getCalendar).mockRejectedValueOnce(new Error("Canvas 离线"));
    vi.mocked(getTimetableSchedule).mockResolvedValue({
      events: [
        {
          id: "real-event",
          title: "真实课表",
          location: null,
          periodLabel: null,
          eventType: "course",
          source: "timetable",
          canonicalCourseId: null,
          startAt: new Date().toISOString(),
          endAt: new Date(Date.now() + 3600000).toISOString(),
          courseName: "课表",
        },
      ],
    });
    render(<V2Overview navigate={vi.fn()} />);
    await screen.findByText("同步索引暂不可用");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("真实课表");
    expect(getTimetableSchedule).toHaveBeenCalled();
  });
  it("点击最近截止使用原作业定位参数", async () => {
    const onAssignment = vi.fn();
    vi.mocked(invoke).mockResolvedValue({
      courses: 1,
      upcoming_deadlines: 1,
      unread_emails: 0,
      messages: [],
      deadlines: [
        {
          source_id: "x",
          title: "真实作业",
          course: "课程",
          due_at: new Date(Date.now() + 3600000).toISOString(),
          submission_state: "unsubmitted",
          url: "https://canvas.test/courses/12/assignments/34",
        },
      ],
    });
    render(<V2Overview navigate={vi.fn()} onAssignment={onAssignment} />);
    fireEvent.click((await screen.findByText("真实作业")).closest("button")!);
    expect(onAssignment).toHaveBeenCalledWith({
      courseId: 12,
      assignmentId: 34,
    });
  });
  it("新壳用独立导航，没有教师管理入口和旧侧栏输入框", () => {
    const onOpenAI = vi.fn();
    const { container } = render(
      <V2Shell
        view="overview"
        setView={vi.fn()}
        drawerOpen={false}
        setDrawerOpen={vi.fn()}
        syncStatus={null}
        syncing={false}
        onSync={vi.fn()}
        onOpenAI={onOpenAI}
      >
        内容
      </V2Shell>,
    );
    fireEvent.click(screen.getByRole("button", { name: "AI 助手" }));
    expect(onOpenAI).toHaveBeenCalled();
    expect(container.querySelector(".v2-sidebar input")).toBeNull();
    expect(screen.queryByText(/花名册|成绩管理|批改中心/)).toBeNull();
  });
  it("弹窗输入不会重新抢焦点，Escape 恢复触发位置", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <V2Dialog title="创建日程" onClose={() => onClose()}>
        <input aria-label="标题" />
      </V2Dialog>,
    );
    const input = screen.getByRole("textbox");
    input.focus();
    rerender(
      <V2Dialog title="创建日程" onClose={() => onClose()}>
        <input aria-label="标题" />
      </V2Dialog>,
    );
    expect(document.activeElement).toBe(input);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  it("设置分类改变时业务表单保持挂载和输入草稿", () => {
    const { container } = render(
      <V2SettingsSurface>
        <section aria-labelledby="appearance-title">
          <input aria-label="外观草稿" defaultValue="" />
        </section>
        <section aria-labelledby="configuration-title">
          <input aria-label="连接草稿" defaultValue="" />
        </section>
      </V2SettingsSurface>,
    );
    fireEvent.click(screen.getByRole("button", { name: "账户与连接" }));
    const input = screen.getByRole("textbox", { name: "连接草稿" });
    fireEvent.change(input, { target: { value: "草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "外观" }));
    expect(container.querySelector('[aria-label="连接草稿"]')).toBe(input);
    fireEvent.click(screen.getByRole("button", { name: "账户与连接" }));
    expect(
      (screen.getByRole("textbox", { name: "连接草稿" }) as HTMLInputElement)
        .value,
    ).toBe("草稿");
  });
  it("个人日程的创建、编辑、查看和删除真实保存在本机存储", async () => {
    render(
      <V2Schedule
        events={[]}
        month={new Date()}
        onMonthChange={vi.fn()}
        onImport={vi.fn()}
        importing={false}
        status=""
        onRetry={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "添加日程" }));
    const form = screen.getByRole("dialog", { name: "创建日程" });
    fireEvent.change(within(form).getByLabelText("标题"), {
      target: { value: "复习安排" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "保存" }));
    await screen.findByRole("dialog", { name: "日程详情" });
    expect(readPersonalEvents()[0].title).toBe("复习安排");
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("标题"), {
      target: { value: "更新后的复习安排" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(readPersonalEvents()).toHaveLength(1);
    expect(readPersonalEvents()[0].title).toBe("更新后的复习安排");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(readPersonalEvents()).toHaveLength(0);
  });
  it("创建弹窗取消后保留标题，并校验时间顺序", () => {
    render(
      <V2Schedule
        events={[]}
        month={new Date()}
        onMonthChange={vi.fn()}
        onImport={vi.fn()}
        importing={false}
        status=""
        onRetry={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "添加日程" }));
    fireEvent.change(screen.getByLabelText("标题"), {
      target: { value: "待完善日程" },
    });
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByRole("button", { name: "添加日程" }));
    expect((screen.getByLabelText("标题") as HTMLInputElement).value).toBe(
      "待完善日程",
    );
    fireEvent.change(screen.getByLabelText("结束"), {
      target: { value: "08:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert").textContent).toContain(
      "结束时间晚于开始时间",
    );
    expect(readPersonalEvents()).toHaveLength(0);
  });
  it("时间网格点击作业跳原提交中心", () => {
    const open = vi.fn(),
      start = new Date();
    render(
      <V2Schedule
        events={[
          {
            id: "assignment-1",
            title: "真实 Canvas 作业",
            startAt: start.toISOString(),
            courseName: "课程",
            eventType: "assignment",
            assignmentId: 1,
            assignmentCourseId: 2,
          },
        ]}
        month={start}
        onMonthChange={vi.fn()}
        onImport={vi.fn()}
        importing={false}
        status=""
        onRetry={vi.fn()}
        onOpenEvent={open}
      />,
    );
    const rail = screen.getByLabelText("00:00至24:00时间网格，可上下滚动");
    fireEvent.click(
      within(rail).getByRole("button", { name: /真实 Canvas 作业/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "打开作业中心" }));
    expect(open).toHaveBeenCalledWith(
      expect.objectContaining({ assignmentId: 1, assignmentCourseId: 2 }),
    );
    fireEvent.click(screen.getByRole("tab", { name: /^日$/ }));
    expect(screen.getByLabelText("00:00至24:00时间网格，可上下滚动")).toBe(
      rail,
    );
    expect(screen.queryByText("双击日期创建日程")).toBeNull();
  });
  it("不可信或损坏的个人日程缓存不会令页面崩溃", () => {
    localStorage.setItem(
      "sjtu-v2-personal-events",
      JSON.stringify([
        null,
        {},
        "x",
        { personal: true, id: "bad", title: "bad", startAt: "invalid" },
      ]),
    );
    expect(readPersonalEvents()).toEqual([]);
  });
});
