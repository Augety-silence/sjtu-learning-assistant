// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
import { cleanup, fireEvent, render, screen } from "@/test/render";

afterEach(cleanup);
it("辅助信息有唯一可访问描述，焦点可显示、Escape可关闭，图标不提交表单", () => {
  const submit = vi.fn((e) => e.preventDefault());
  render(
    <form onSubmit={submit}>
      <InfoTooltip label="同步详情">同步时间</InfoTooltip>
      <InfoTooltip label="用量说明">缓存是输入子集</InfoTooltip>
    </form>,
  );
  const info = screen.getByRole("button", { name: "同步详情" });
  const other = screen.getByRole("button", { name: "用量说明" });
  expect(info.getAttribute("aria-describedby")).not.toBe(
    other.getAttribute("aria-describedby"),
  );
  expect(
    document.getElementById(info.getAttribute("aria-describedby") || "")
      ?.textContent,
  ).toBe("同步时间");
  expect(info.getAttribute("type")).toBe("button");
  fireEvent.focus(info);
  fireEvent.keyDown(info, { key: "Escape" });
  expect(info.parentElement?.dataset.dismissed).toBe("true");
  fireEvent.mouseEnter(info.parentElement as HTMLElement);
  expect(info.parentElement?.dataset.dismissed).toBe("false");
  fireEvent.click(info);
  expect(submit).not.toHaveBeenCalled();
});

it("提示挂在全局层而不是裁切父级，贴近右下角时仍在视口内", () => {
  const view = render(
    <div style={{ overflow: "hidden", transform: "translateX(0)" }}>
      <InfoTooltip label="说明">很长的说明文字</InfoTooltip>
    </div>,
  );
  const button = screen.getByRole("button", { name: "说明" });
  const tooltip = document.getElementById(
    button.getAttribute("aria-describedby") || "",
  )!;
  expect(tooltip.parentElement).toBe(document.body);
  expect(view.container.contains(tooltip)).toBe(false);
  vi.spyOn(button.parentElement!, "getBoundingClientRect").mockReturnValue({
    left: 1000,
    right: 1020,
    top: 740,
    bottom: 760,
  } as DOMRect);
  vi.spyOn(tooltip, "getBoundingClientRect").mockReturnValue({
    width: 320,
    height: 100,
  } as DOMRect);
  fireEvent.mouseEnter(button.parentElement!);
  expect(tooltip.hidden).toBe(false);
  expect(parseFloat(tooltip.style.left)).toBeLessThanOrEqual(
    window.innerWidth - 332,
  );
  expect(parseFloat(tooltip.style.top)).toBeLessThanOrEqual(
    window.innerHeight - 112,
  );
  fireEvent.keyDown(button, { key: "Escape" });
  expect(tooltip.hidden).toBe(true);
  view.unmount();
  expect(document.body.contains(tooltip)).toBe(false);
  vi.restoreAllMocks();
});
