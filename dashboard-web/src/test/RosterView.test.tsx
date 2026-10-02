// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { type RosterMember, RosterView } from "@/components/RosterView";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

afterEach(cleanup);

const members: RosterMember[] = [
  {
    id: "1",
    name: "张同学",
    loginId: "521001",
    email: "zhang@example.edu",
    role: "学生",
    section: "一班",
  },
  {
    id: "2",
    name: "李老师",
    loginId: "teacher",
    role: "教师",
    section: "一班",
  },
];

describe("RosterView", () => {
  it("filters by text and role", () => {
    render(<RosterView members={members} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索课程成员" }), {
      target: { value: "521001" },
    });
    expect(screen.getByText("张同学")).toBeTruthy();
    expect(screen.queryByText("李老师")).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "按角色筛选" }), {
      target: { value: "教师" },
    });
    expect(screen.getByText("没有匹配成员")).toBeTruthy();
  });

  it("exports selected members", async () => {
    const exportMembers = vi.fn(async () => undefined);
    render(<RosterView members={members} onExport={exportMembers} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 张同学" }));
    fireEvent.click(screen.getByRole("button", { name: /导出所选/ }));
    await waitFor(() =>
      expect(exportMembers).toHaveBeenCalledWith({
        members: [members[0]],
        scope: "selected",
        format: "csv",
      }),
    );
  });
});
