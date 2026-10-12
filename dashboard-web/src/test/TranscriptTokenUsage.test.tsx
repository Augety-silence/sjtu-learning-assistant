// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { TranscriptPhase1Panel } from "@/components/TranscriptPhase1Panels";
import type { Phase1ArtifactRead } from "@/lib/types";
import { cleanup, render, screen, within } from "@/test/render";

afterEach(cleanup);
function show(data: unknown[]) {
  const artifact: Phase1ArtifactRead = {
    id: "synthetic-usage",
    kind: "pipeline_events",
    content: JSON.stringify(data),
    version: "v2",
    content_type: "application/json",
    size: 100,
    sha256: "a".repeat(64),
    data,
  };
  render(
    <TranscriptPhase1Panel
      kind="pipeline_events"
      artifact={artifact}
      manifestWarnings={[]}
    />,
  );
}
function metric(name: string) {
  const term = screen.getByText(name, { selector: "dt" });
  return within(term.parentElement as HTMLElement).getByRole("definition")
    .textContent;
}
describe("AI 校对 Token 用量", () => {
  it("includes paid schema failures and excludes deterministic metrics or skipped roles", () => {
    show([
      {
        agent: "terminology",
        status: "schema_error",
        token_usage: {
          prompt_tokens: 20,
          completion_tokens: 4,
          total_tokens: 24,
        },
      },
      {
        agent: "correction",
        status: "ok",
        token_usage: {
          prompt_tokens: 30,
          completion_tokens: 5,
          total_tokens: 35,
        },
      },
      { agent: "memory", status: "skipped", token_usage: {} },
      { agent: "program", token_usage: { total_tokens: 999, pack_count: 9 } },
    ]);
    expect(metric("校对调用")).toBe("2 次");
    expect(metric("输入 Token")).toBe("50");
    expect(metric("输出 Token")).toBe("9");
    expect(metric("服务端总 Token")).toBe("59");
  });
  it("reports partial known totals and retains timeouts as unknown", () => {
    show([
      { agent: "terminology", status: "ok", token_usage: { total_tokens: 10 } },
      { agent: "critic", status: "timeout", token_usage: {} },
    ]);
    expect(metric("服务端总 Token")).toBe("10（已知部分，1 次未知）");
    expect(metric("输入 Token")).toBe("未知");
    expect(screen.getByText(/原始字幕整理和本节小结不在此统计内/)).toBeTruthy();
  });
  it("does not infer totals or accept strings, negative counts, or unsafe integers", () => {
    show([
      {
        agent: "correction",
        status: "ok",
        token_usage: {
          prompt_tokens: 0,
          completion_tokens: 5,
          total_tokens: "5",
        },
      },
      {
        agent: "critic",
        status: "error",
        token_usage: {
          prompt_tokens: -1,
          completion_tokens: Number.MAX_SAFE_INTEGER + 1,
        },
      },
    ]);
    expect(metric("服务端总 Token")).toBe("未知");
    expect(metric("输入 Token")).toBe("0（已知部分，1 次未知）");
    expect(metric("输出 Token")).toBe("5（已知部分，1 次未知）");
  });
  it("does not claim zero consumption for old artifacts without semantic events", () => {
    show([{ agent: "program", status: "ok", token_usage: {} }]);
    expect(screen.getByText(/用量未知/)).toBeTruthy();
    expect(screen.queryByText("0 次")).toBeNull();
  });
});
