// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { FilePreviewDialog } from "@/components/FilePreviewDialog";
import type { MaterialPreview } from "@/lib/types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

afterEach(cleanup);

describe("FilePreviewDialog ready reveal", () => {
  it("预览层直接挂载到 body，脱离正文层叠且保留关闭焦点", async () => {
    const close = vi.fn();
    const { container } = render(
      <div className="content" style={{ position: "relative", zIndex: 1 }}>
        <FilePreviewDialog
          preview={{ kind: "text", name: "课程.txt", text: "正文" }}
          onClose={close}
        />
      </div>,
    );
    const dialog = screen.getByRole("dialog", { name: "课程.txt" });
    expect(dialog.closest("[data-modal-layer]")?.parentElement).toBe(
      document.body,
    );
    expect(container.contains(dialog)).toBe(false);
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "关闭文件预览" }),
      ),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
  });

  it("图片解码期间保留关闭按钮，完成后仅显现正文", async () => {
    let finishDecode: (() => void) | undefined;
    const close = vi.fn();
    render(
      <FilePreviewDialog
        preview={{
          kind: "image",
          name: "课程图片.png",
          data_url: "data:image/png;base64,test",
        }}
        onClose={close}
      />,
    );
    const image = screen.getByRole("img", {
      name: "课程图片.png",
    }) as HTMLImageElement;
    image.decode = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishDecode = resolve;
        }),
    );
    const body = image.closest("[data-content-reveal]") as HTMLElement;
    expect(body.getAttribute("data-content-reveal")).toBe("loading");
    fireEvent.load(image);
    expect(screen.getByText("正在准备预览内容…")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭文件预览" }));
    expect(close).toHaveBeenCalledOnce();
    await act(async () => finishDecode?.());
    await waitFor(() =>
      expect(body.getAttribute("data-content-reveal")).toBe("ready"),
    );
    expect(body.style.getPropertyValue("--content-reveal-duration")).toBe(
      "360ms",
    );
    expect(screen.queryByText("正在准备预览内容…")).toBeNull();
  });

  it("旧图片的晚到解码不能提前显现新预览", async () => {
    let finishOld: (() => void) | undefined;
    const oldPreview: MaterialPreview = {
      kind: "image",
      name: "旧图片.png",
      data_url: "data:image/png;base64,old",
    };
    const { rerender } = render(
      <FilePreviewDialog preview={oldPreview} onClose={vi.fn()} />,
    );
    const image = screen.getByRole("img") as HTMLImageElement;
    image.decode = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishOld = resolve;
        }),
    );
    fireEvent.load(image);
    rerender(
      <FilePreviewDialog
        preview={{
          kind: "image",
          name: "新图片.png",
          data_url: "data:image/png;base64,new",
        }}
        onClose={vi.fn()}
      />,
    );
    await act(async () => finishOld?.());
    expect(
      image
        .closest("[data-content-reveal]")
        ?.getAttribute("data-content-reveal"),
    ).toBe("loading");
    image.decode = vi.fn().mockResolvedValue(undefined);
    fireEvent.load(image);
    await waitFor(() =>
      expect(
        image
          .closest("[data-content-reveal]")
          ?.getAttribute("data-content-reveal"),
      ).toBe("ready"),
    );
  });

  it("同 URL 的新对象保留就绪状态，变更 URL 才重新等待", async () => {
    const original: MaterialPreview = {
      kind: "image",
      name: "课程图片.png",
      data_url: "data:image/png;base64,original",
    };
    const { rerender } = render(
      <FilePreviewDialog preview={original} onClose={vi.fn()} />,
    );
    const image = screen.getByRole("img") as HTMLImageElement;
    const body = image.closest("[data-content-reveal]") as HTMLElement;
    image.decode = vi.fn().mockResolvedValue(undefined);
    fireEvent.load(image);
    await waitFor(() => expect(body.getAttribute("aria-busy")).toBe("false"));

    rerender(<FilePreviewDialog preview={{ ...original }} onClose={vi.fn()} />);
    expect(screen.getByRole("img")).toBe(image);
    expect(image.closest("[data-content-reveal]")).toBe(body);
    expect(body.getAttribute("data-content-reveal")).toBe("ready");
    expect(body.getAttribute("aria-busy")).toBe("false");
    expect(screen.queryByText("正在准备预览内容…")).toBeNull();
    expect(image.decode).toHaveBeenCalledOnce();

    rerender(
      <FilePreviewDialog
        preview={{ ...original, data_url: "data:image/png;base64,changed" }}
        onClose={vi.fn()}
      />,
    );
    expect(body.getAttribute("data-content-reveal")).toBe("loading");
    expect(body.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("正在准备预览内容…")).toBeTruthy();
    fireEvent.load(image);
    await waitFor(() => expect(body.getAttribute("aria-busy")).toBe("false"));
    expect(body.getAttribute("data-content-reveal")).toBe("ready");
  });

  it("PDF容器加载信号触发正文显现，框体始终可关闭", () => {
    render(
      <FilePreviewDialog
        preview={{
          kind: "pdf",
          name: "课程.pdf",
          data_url: "data:application/pdf;base64,test",
        }}
        onClose={vi.fn()}
      />,
    );
    const frame = screen.getByTitle("课程.pdf PDF 预览") as HTMLIFrameElement;
    expect(screen.getByRole("dialog", { name: "课程.pdf" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "关闭文件预览" })).toBeTruthy();
    expect(
      frame
        .closest("[data-content-reveal]")
        ?.getAttribute("data-content-reveal"),
    ).toBe("loading");
    fireEvent.load(frame);
    expect(
      frame
        .closest("[data-content-reveal]")
        ?.getAttribute("data-content-reveal"),
    ).toBe("ready");
  });

  it("已准备好的文本不等待媒体信号", () => {
    render(
      <FilePreviewDialog
        preview={{ kind: "text", name: "课程.txt", text: "已准备好的正文" }}
        onClose={vi.fn()}
      />,
    );
    const content = screen.getByText("已准备好的正文");
    expect(
      content
        .closest("[data-content-reveal]")
        ?.getAttribute("data-content-reveal"),
    ).toBe("ready");
    expect(screen.queryByText("正在准备预览内容…")).toBeNull();
  });
});
