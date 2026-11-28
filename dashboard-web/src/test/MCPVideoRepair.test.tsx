// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { createRef } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { AIExternalMCP } from "@/components/AIExternalMCP";
import { captionAt, VideoCaptions } from "@/components/VideoCaptions";
import { parseVttCues } from "@/lib/videoSubtitles";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ invoke }));
afterEach(() => {
  cleanup();
  invoke.mockReset();
});

it("自动字幕跟随跳转；长段完整保留并重置局部滚动", async () => {
  const video = document.createElement("video");
  const ref = createRef<HTMLVideoElement>();
  ref.current = video;
  const long = "长字幕正文".repeat(60);
  render(
    <VideoCaptions
      videoRef={ref}
      cues={[
        { startMs: 0, endMs: 2000, text: long },
        { startMs: 3000, endMs: 5000, text: "第二段" },
      ]}
    />,
  );
  expect(screen.getByTestId("video-caption-overlay").textContent).toBe(long);
  screen.getByTestId("video-caption-overlay").scrollTop = 100;
  video.currentTime = 3;
  fireEvent(video, new Event("seeked"));
  await waitFor(() =>
    expect(screen.getByTestId("video-caption-overlay").textContent).toBe(
      "第二段",
    ),
  );
  expect(screen.getByTestId("video-caption-overlay").scrollTop).toBe(0);
  video.currentTime = 2;
  fireEvent(video, new Event("timeupdate"));
  expect(screen.queryByTestId("video-caption-overlay")).toBeNull();
});
it("原始VTT每段起止定位保持一致，拒绝反向区间", () => {
  const vtt =
    "WEBVTT\n\n00:00:00.000 --> 00:00:41.660\n第一段\n\n00:00:41.660 --> 00:01:18.040\n第二段\n\n00:01:18.540 --> 00:01:50.830\n第三段\n\n00:02:00.000 --> 00:01:00.000\n无效\n";
  const cues = parseVttCues(vtt);
  expect(cues).toHaveLength(3);
  for (const cue of cues) {
    expect(captionAt(cues, cue.startMs / 1000)).toBe(cue.text);
    expect(captionAt(cues, (cue.endMs - 1) / 1000)).toBe(cue.text);
  }
  expect(captionAt(cues, 78.2)).toBe("");
});
it("Bing配置来自本机启动入口，不硬编码设备路径", async () => {
  invoke.mockImplementation(async (action) =>
    action === "ai_mcp_template"
      ? {
          config:
            '{"mcpServers":{"Bing":{"command":"device-binary","args":["--web-search-mcp"]}}}',
        }
      : { items: [] },
  );
  render(<AIExternalMCP />);
  fireEvent.click(screen.getByRole("button", { name: "添加 / 导入" }));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Bing 搜索模板",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Bing 搜索模板" }));
  await waitFor(() =>
    expect(invoke).toHaveBeenCalledWith("ai_mcp_template", {
      provider: "bing",
    }),
  );
  await waitFor(() =>
    expect(
      (screen.getByLabelText("MCP JSON 配置") as HTMLTextAreaElement).value,
    ).toContain("device-binary"),
  );
});
it("MCP搜索业务失败显示失败状态与原始原因", async () => {
  invoke.mockImplementation(async (action) =>
    action === "ai_mcp_test"
      ? { text: "上游可能拦截", isError: true }
      : {
          items: [
            {
              id: "DuckDuckGo",
              name: "DuckDuckGo",
              enabled: true,
              permission: "read_only",
              transport: "stdio",
              command: "uvx",
              env_keys: [],
              tools: [
                {
                  name: "search",
                  enabled: true,
                  read_only: true,
                  description: "搜索",
                },
              ],
            },
          ],
        },
  );
  render(<AIExternalMCP />);
  await screen.findByRole("switch", { name: "启用 MCP DuckDuckGo" });
  await waitFor(() =>
    expect(
      (
        screen.getByRole("switch", {
          name: "启用 MCP DuckDuckGo",
        }) as HTMLInputElement
      ).disabled,
    ).toBe(false),
  );
  fireEvent.click(
    document.querySelector(".extension-row-info") as HTMLButtonElement,
  );
  fireEvent.click(await screen.findByRole("button", { name: "测试搜索" }));
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain("搜索未完成"),
  );
  expect(screen.getByLabelText("MCP 搜索结果").textContent).toContain(
    "上游可能拦截",
  );
});
