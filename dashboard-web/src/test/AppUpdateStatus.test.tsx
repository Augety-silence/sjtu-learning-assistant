// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppUpdateStatus } from "@/components/AppUpdateStatus";
import { invoke } from "@/lib/api";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({ invoke: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
describe("自动更新状态", () => {
  it("展示官方下载进度并允许取消，不暴露下载路径", async () => {
    vi.mocked(invoke).mockImplementation(async (action) =>
      action === "update_cancel"
        ? { phase: "cancelled", message: "更新已取消" }
        : {
            phase: "downloading",
            message: "正在下载新版",
            received: 25,
            total: 100,
          },
    );
    render(<AppUpdateStatus />);
    await screen.findByText("正在下载新版");
    expect(screen.getByRole("progressbar").getAttribute("value")).toBe("25");
    fireEvent.click(screen.getByRole("button", { name: "取消更新" }));
    await screen.findByText("更新已取消");
    expect(invoke).toHaveBeenCalledWith("update_cancel", {});
  });
  it("安装阶段不允许取消", async () => {
    vi.mocked(invoke).mockResolvedValue({
      phase: "installing",
      message: "正在安装新版",
    });
    render(<AppUpdateStatus />);
    await screen.findByText("正在安装新版");
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("失败时保留错误说明", async () => {
    vi.mocked(invoke).mockResolvedValue({
      phase: "failed",
      message: "校验失败，原版本仍可使用",
    });
    render(<AppUpdateStatus />);
    await waitFor(() =>
      expect(screen.getByText("校验失败，原版本仍可使用")).toBeTruthy(),
    );
  });
});
