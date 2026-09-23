// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssignmentsView } from "@/components/AssignmentsView";
import { PanFilePicker } from "@/components/PanFilePicker";
import * as api from "@/lib/api";
import type { AssignmentItem } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getAssignments: vi.fn(),
  getAssignmentDetail: vi.fn(),
  openExternalAssignment: vi.fn(),
  pickAssignmentLocalFile: vi.fn(),
  submitAssignmentCloudFile: vi.fn(),
  submitAssignmentLocalFile: vi.fn(),
  submitAssignmentText: vi.fn(),
  submitAssignmentUrl: vi.fn(),
  getPanFiles: vi.fn(),
}));

const assignment: AssignmentItem = {
  course_id: "1",
  course_name: "人机交互",
  id: "2",
  name: "项目报告",
  description: "完成报告",
  due_at: "2026-09-23T12:00:00+08:00",
  unlock_at: null,
  lock_at: null,
  points_possible: 100,
  html_url: "https://oc.sjtu.edu.cn/courses/1/assignments/2",
  submission_types: ["online_text_entry"],
  submission: {
    id: null,
    workflow_state: "unsubmitted",
    submission_type: null,
    submitted_at: null,
    attempt: null,
    missing: false,
    late: false,
    score: null,
    grade: null,
    attachments: [],
  },
  can_submit: true,
  requires_external_submission: false,
  categories: ["today", "unsubmitted"],
};

afterEach(cleanup);

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getAssignments).mockResolvedValue({
    category: "today",
    items: [assignment],
  });
  vi.mocked(api.getAssignmentDetail).mockResolvedValue(assignment);
});

describe("AssignmentsView", () => {
  it("offers all eight classifications and reloads the selected category", async () => {
    render(<AssignmentsView />);
    for (const label of [
      "今天",
      "即将截止",
      "已逾期",
      "缺交",
      "未提交",
      "已提交",
      "待批改",
      "已评分",
    ]) {
      expect(screen.getByRole("tab", { name: label })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("tab", { name: "已评分" }));
    await waitFor(() =>
      expect(api.getAssignments).toHaveBeenLastCalledWith("graded"),
    );
  });

  it("shows external submissions and opens the Canvas assignment", async () => {
    const external = {
      ...assignment,
      submission_types: ["external_tool"],
      can_submit: false,
      requires_external_submission: true,
    };
    vi.mocked(api.getAssignments).mockResolvedValue({
      category: "today",
      items: [external],
    });
    vi.mocked(api.getAssignmentDetail).mockResolvedValue(external);
    vi.mocked(api.openExternalAssignment).mockResolvedValue({
      verified: false,
      status: "requires_external_submission",
      message: "已打开",
      submission_type: "external_tool",
      submission_id: null,
      submitted_at: null,
      attempt: null,
      attachments: [],
      workflow_state: null,
    });
    render(<AssignmentsView />);
    fireEvent.click(await screen.findByRole("button", { name: /项目报告/ }));
    expect(
      await screen.findByText("此作业需要在 Canvas 外部页面完成提交。"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /在 Canvas 打开/ }));
    await waitFor(() =>
      expect(api.openExternalAssignment).toHaveBeenCalledWith(1, 2),
    );
  });

  it("requires confirmation and only displays verified success", async () => {
    vi.mocked(api.submitAssignmentText).mockResolvedValue({
      verified: true,
      status: "verified",
      message: null,
      submission_type: "online_text_entry",
      submission_id: 99,
      submitted_at: "2026-09-23T03:00:00Z",
      attempt: 1,
      attachments: [],
      workflow_state: "submitted",
    });
    render(<AssignmentsView />);
    fireEvent.click(await screen.findByRole("button", { name: /项目报告/ }));
    fireEvent.change(await screen.findByLabelText("文本内容"), {
      target: { value: "我的答案" },
    });
    fireEvent.click(screen.getByRole("button", { name: "准备提交文本" }));
    expect(screen.getByRole("alertdialog").textContent).toContain("人机交互");
    expect(screen.getByRole("alertdialog").textContent).toContain("文本提交");
    fireEvent.click(screen.getByRole("button", { name: "确认提交" }));
    expect(await screen.findByText("提交已由 Canvas 验证")).toBeTruthy();
    expect(screen.getByText("提交 ID：99")).toBeTruthy();
  });

  it("requires confirmation for URL, local file, and Pan file", async () => {
    const allTypes = {
      ...assignment,
      submission_types: ["online_url", "online_upload"],
    };
    vi.mocked(api.getAssignments).mockResolvedValue({
      category: "today",
      items: [allTypes],
    });
    vi.mocked(api.getAssignmentDetail).mockResolvedValue(allTypes);
    vi.mocked(api.pickAssignmentLocalFile).mockResolvedValue({
      cancelled: false,
      path: "/safe/report.pdf",
      name: "report.pdf",
      size: 12,
    });
    vi.mocked(api.getPanFiles).mockResolvedValue({
      remote_path: "",
      page: 1,
      page_size: 50,
      total: 1,
      has_more: false,
      items: [
        {
          remote_path: "cloud.pdf",
          name: "cloud.pdf",
          is_directory: false,
          size: 12,
          modified_at: null,
        },
      ],
    });
    render(<AssignmentsView />);
    fireEvent.click(await screen.findByRole("button", { name: /项目报告/ }));

    fireEvent.change(await screen.findByLabelText("作业网址"), {
      target: { value: "https://example.edu/work" },
    });
    fireEvent.click(screen.getByRole("button", { name: "准备提交网址" }));
    expect(api.submitAssignmentUrl).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog").textContent).toContain("网址提交");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(screen.getByRole("button", { name: "选择本地文件" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "准备提交本地文件" }),
    );
    expect(api.submitAssignmentLocalFile).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog").textContent).toContain("report.pdf");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(screen.getByRole("button", { name: "从交大云盘选择" }));
    fireEvent.click(
      (await screen.findByText("cloud.pdf")).closest(
        "button",
      ) as HTMLButtonElement,
    );
    expect(api.submitAssignmentCloudFile).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog").textContent).toContain("cloud.pdf");
  });

  it("does not show success when verification is false", async () => {
    vi.mocked(api.submitAssignmentText).mockResolvedValue({
      verified: false,
      status: "unverified",
      message: "验证失败",
      submission_type: "online_text_entry",
      submission_id: null,
      submitted_at: null,
      attempt: null,
      attachments: [],
      workflow_state: null,
    });
    render(<AssignmentsView />);
    fireEvent.click(await screen.findByRole("button", { name: /项目报告/ }));
    fireEvent.change(await screen.findByLabelText("文本内容"), {
      target: { value: "我的答案" },
    });
    fireEvent.click(screen.getByRole("button", { name: "准备提交文本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认提交" }));
    await waitFor(() => expect(api.submitAssignmentText).toHaveBeenCalled());
    expect(screen.queryByText("提交已由 Canvas 验证")).toBeNull();
  });
});

describe("PanFilePicker", () => {
  it("navigates directories and selects a cloud file", async () => {
    vi.mocked(api.getPanFiles)
      .mockResolvedValueOnce({
        remote_path: "",
        page: 1,
        page_size: 50,
        total: 1,
        has_more: false,
        items: [
          {
            remote_path: "课程",
            name: "课程",
            is_directory: true,
            size: null,
            modified_at: null,
          },
        ],
      })
      .mockResolvedValueOnce({
        remote_path: "课程",
        page: 1,
        page_size: 50,
        total: 1,
        has_more: false,
        items: [
          {
            remote_path: "课程/report.pdf",
            name: "report.pdf",
            is_directory: false,
            size: 12,
            modified_at: "2026-09-23",
          },
        ],
      });
    const selected = vi.fn();
    render(<PanFilePicker onSelect={selected} />);
    fireEvent.click(
      (await screen.findByText("课程")).closest("button") as HTMLButtonElement,
    );
    fireEvent.click(
      (await screen.findByText("report.pdf")).closest(
        "button",
      ) as HTMLButtonElement,
    );
    expect(selected).toHaveBeenCalledWith(
      expect.objectContaining({ remote_path: "课程/report.pdf" }),
    );
  });
});
