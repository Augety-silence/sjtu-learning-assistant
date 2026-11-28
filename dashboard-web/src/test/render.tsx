// 既有测试验证保留的经典业务；2.0 测试显式切换版本。
if (typeof document !== "undefined")
  document.documentElement.dataset.uiRelease = "classic";

import {
  type RenderOptions,
  render as testingLibraryRender,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { ToastProvider } from "@/components/Toast";

export * from "@testing-library/react";

export function render(ui: ReactElement, options?: RenderOptions) {
  const wrap = (element: ReactElement) => (
    <ToastProvider>{element}</ToastProvider>
  );
  const result = testingLibraryRender(wrap(ui), options);
  return {
    ...result,
    rerender: (nextUi: ReactElement) => result.rerender(wrap(nextUi)),
  };
}
