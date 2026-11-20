// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CourseMaterialsView } from "@/components/CourseMaterialsView";
import {
  type CourseScope,
  CourseScopePicker,
} from "@/components/CourseScopePicker";
import { invoke } from "@/lib/api";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({ invoke: vi.fn() }));
vi.mock("@/components/MaterialsView", () => ({
  MaterialsView: ({
    termId,
    courseId,
  }: {
    termId: string;
    courseId: string;
  }) => (
    <p data-testid="scope">
      {termId}/{courseId}
    </p>
  ),
}));
const catalog = {
  count: 2,
  complete: true,
  refreshed_at: null,
  terms: [
    {
      id: "term:10",
      name: "2026 春",
      courses: [
        { id: "1", name: "数据库", current: false, access: "available" },
      ],
    },
    {
      id: "term:20",
      name: "2026 秋",
      courses: [
        { id: "2", name: "数据库", current: true, access: "restricted" },
      ],
    },
  ],
};
const idle = { status: "idle", completed: 0, total: 0 };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(invoke).mockImplementation(
    async (operation) =>
      (operation === "course_catalog" ? catalog : idle) as never,
  );
});
afterEach(cleanup);
function Picker() {
  const [scope, setScope] = useState<CourseScope>({ termId: "", courseId: "" });
  return <CourseScopePicker scope={scope} onChange={setScope} />;
}
describe("历史课程入口", () => {
  it("默认只读缓存，显式发现全部学期；切学期清除旧课程", async () => {
    render(<Picker />);
    await screen.findByRole("option", { name: /2026 春/ });
    expect(invoke).toHaveBeenCalledWith("course_catalog", { refresh: false });
    const term = screen.getByLabelText("课程学期");
    fireEvent.change(term, { target: { value: "term:10" } });
    const course = screen.getByLabelText("学期课程") as HTMLSelectElement;
    fireEvent.change(course, { target: { value: "1" } });
    fireEvent.change(term, { target: { value: "term:20" } });
    expect(course.value).toBe("");
    expect(screen.getByRole("option", { name: /日期受限/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "发现全部学期" }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("course_catalog", { refresh: true }),
    );
  });
  it("无学期禁止批量同步，发送固定课程范围和下载选项", async () => {
    vi.mocked(invoke).mockImplementation(async (operation) => {
      if (operation === "course_catalog") return catalog as never;
      if (operation === "course_history_start")
        return {
          status: "running",
          course_ids: ["1"],
          completed: 0,
          total: 1,
        } as never;
      return idle as never;
    });
    render(<CourseMaterialsView />);
    expect(
      (
        screen.getByRole("button", {
          name: "同步并下载所选课件",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    await screen.findByRole("option", { name: /2026 春/ });
    fireEvent.change(screen.getByLabelText("课程学期"), {
      target: { value: "term:10" },
    });
    fireEvent.change(screen.getByLabelText("学期课程"), {
      target: { value: "1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "同步并下载所选课件" }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("course_history_start", {
        term_id: "term:10",
        course_ids: ["1"],
        download: true,
      }),
    );
    await screen.findByText(/任务固定范围：1 门/);
    fireEvent.change(screen.getByLabelText("课程学期"), {
      target: { value: "term:20" },
    });
    expect(screen.getByTestId("scope").textContent).toBe("term:20/");
    expect(screen.getByText(/任务固定范围：1 门/)).toBeTruthy();
  });
  it("刷新失败保留缓存并展示可重试错误", async () => {
    render(<Picker />);
    await screen.findByRole("option", { name: /2026 春/ });
    vi.mocked(invoke).mockRejectedValueOnce(new Error("凭据过期"));
    fireEvent.click(screen.getByRole("button", { name: "发现全部学期" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "凭据过期",
    );
    expect(screen.getByRole("option", { name: /2026 春/ })).toBeTruthy();
  });
});
