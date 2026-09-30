// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { GradesView } from "@/components/GradesView";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

afterEach(cleanup);

const assignments = [{ id: "a1", name: "项目报告", pointsPossible: 100 }];
const students = [
  {
    studentId: "s1",
    studentName: "张同学",
    loginId: "123",
    grades: { a1: { score: 88, status: "graded" as const, editable: true } },
  },
];

describe("GradesView", () => {
  it("shows summary, student grades and statistics", () => {
    render(<GradesView assignments={assignments} students={students} />);
    expect(screen.getByRole("table", { name: "学生成绩表" })).toBeTruthy();
    expect(screen.getByText("88")).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "统计" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(
      screen.getByRole("table", { name: "作业成绩统计" }).textContent,
    ).toContain("88.0");
  });

  it("validates and saves an edited score with typed identifiers", async () => {
    const save = vi.fn(async () => undefined);
    render(
      <GradesView
        assignments={assignments}
        students={students}
        onSaveGrade={save}
      />,
    );
    const input = screen.getByRole("textbox", {
      name: "张同学，项目报告，分数",
    });
    fireEvent.change(input, { target: { value: "92" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        studentId: "s1",
        assignmentId: "a1",
        score: 92,
      }),
    );
  });
});
