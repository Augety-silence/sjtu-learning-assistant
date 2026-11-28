// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { VideoTranscript } from "@/components/VideoTranscript";

afterEach(cleanup);
it("字幕时间标签支持点击和键盘定位，并标识当前播放段", () => {
  const seek = vi.fn();
  render(
    <VideoTranscript
      cues={[
        { startMs: 1000, endMs: 3000, text: "第一段" },
        { startMs: 4000, endMs: 6000, text: "第二段" },
      ]}
      playbackTime={2}
      onSeek={seek}
    />,
  );
  const first = screen.getByRole("button", { name: "跳转到 00:01：第一段" });
  expect(first.getAttribute("aria-current")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "跳转到 00:04：第二段" }));
  expect(seek).toHaveBeenLastCalledWith(4);
  fireEvent.keyDown(first, { key: "Enter" });
  expect(seek).toHaveBeenLastCalledWith(1);
  fireEvent.keyDown(first, { key: " " });
  expect(seek).toHaveBeenCalledTimes(3);
});
