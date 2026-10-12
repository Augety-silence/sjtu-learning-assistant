// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import appLogo from "@/assets/app-logo.png";
import { AppBrand } from "@/components/AppBrand";
import { cleanup, fireEvent, render, screen, within } from "@/test/render";

afterEach(cleanup);

describe("shared app brand", () => {
  it("uses the same logo and wordmark in the workbench and assistant", () => {
    const { container } = render(
      <>
        <AppBrand />
        <AppBrand onClick={vi.fn()} />
      </>,
    );
    const brands = container.querySelectorAll<HTMLElement>(".app-brand");
    expect(brands).toHaveLength(2);
    for (const brand of brands) {
      expect(
        within(brand)
          .getByRole("img", { name: "SJTU 学习助手标志" })
          .getAttribute("src"),
      ).toBe(appLogo);
      expect(within(brand).getByText("SJTU")).toBeTruthy();
      expect(within(brand).getByText("学习助手")).toBeTruthy();
    }
  });

  it("keeps the workbench brand informational when no return action exists", () => {
    render(<AppBrand />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByRole("img").closest("[tabindex]")).toBeNull();
  });

  it("offers an accessible return button without submitting its surrounding form", () => {
    const onBack = vi.fn();
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <AppBrand onClick={onBack} />
      </form>,
    );
    const button = screen.getByRole("button", { name: "返回学习助手" });
    button.focus();
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    expect(onBack).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
