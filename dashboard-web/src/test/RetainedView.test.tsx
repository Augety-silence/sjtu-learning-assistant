// @vitest-environment jsdom
import { useEffect, useRef } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { RetainedView } from "@/components/RetainedView";
import { cleanup, render } from "@/test/render";

afterEach(cleanup);
it("切换整个工作区仍保留同一个 DOM，离开时不可见，退出时释放", () => {
  const mount = vi.fn();
  const unmount = vi.fn();
  function Media() {
    useEffect(() => {
      mount();
      return unmount;
    }, []);
    return <video data-testid="retained-media" />;
  }
  function Host({ active }: { active: boolean }) {
    const target = useRef<HTMLDivElement>(null);
    return (
      <>
        {active && <div key={String(active)} ref={target} />}
        <RetainedView active={active} target={target}>
          <Media />
        </RetainedView>
      </>
    );
  }
  const view = render(<Host active />);
  const media = document.querySelector("video")!;
  media.currentTime = 42;
  view.rerender(<Host active={false} />);
  expect(document.querySelector("video")).toBeNull();
  expect(unmount).not.toHaveBeenCalled();
  view.rerender(<Host active />);
  expect(document.querySelector("video")).toBe(media);
  expect(media.currentTime).toBe(42);
  expect(mount).toHaveBeenCalledOnce();
  view.unmount();
  expect(unmount).toHaveBeenCalledOnce();
});
