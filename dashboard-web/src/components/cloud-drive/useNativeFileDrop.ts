import {
  type DragEvent as ReactDragEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

export const NATIVE_FILE_DROP_EVENT = "sjtu-native-file-drop";

export interface NativeDroppedFile {
  path: string;
  drop_token: string;
}

function parseDetail(detail: unknown): NativeDroppedFile[] | null {
  if (
    typeof detail !== "object" ||
    detail === null ||
    !("files" in detail) ||
    !Array.isArray(detail.files)
  )
    return null;
  const files = detail.files as unknown[];
  const valid = files.every(
    (file) =>
      typeof file === "object" &&
      file !== null &&
      "path" in file &&
      typeof file.path === "string" &&
      "drop_token" in file &&
      typeof file.drop_token === "string",
  );
  return valid ? (files as NativeDroppedFile[]) : null;
}

/**
 * 桌面壳拖放：WebView 的 drop 事件拿不到本地路径，pywebview 会另行派发
 * `sjtu-native-file-drop`（含一次性 drop_token）。两者先后顺序不固定，
 * 因此这里同时处理“原生事件先到”和“DOM drop 先到”两种时序。
 */
export function useNativeFileDrop({
  enabled,
  onFiles,
  onUnavailable,
}: {
  enabled: boolean;
  onFiles: (files: NativeDroppedFile[]) => void;
  onUnavailable: () => void;
}) {
  const [active, setActive] = useState(false);
  const depthRef = useRef(0);
  const pendingRef = useRef<NativeDroppedFile[] | null>(null);
  const awaitingRef = useRef(false);
  const awaitingHandlerRef = useRef<typeof onFiles | null>(null);
  const timerRef = useRef<number | null>(null);
  const onFilesRef = useRef(onFiles);
  const onUnavailableRef = useRef(onUnavailable);
  onFilesRef.current = onFiles;
  onUnavailableRef.current = onUnavailable;

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    const handle = (event: Event) => {
      const files = parseDetail((event as CustomEvent<unknown>).detail);
      if (!files) return;
      if (!awaitingRef.current) {
        if (depthRef.current > 0) pendingRef.current = files;
        return;
      }
      awaitingRef.current = false;
      clearTimer();
      const handler = awaitingHandlerRef.current;
      awaitingHandlerRef.current = null;
      if (files.length > 0) handler?.(files);
    };
    window.addEventListener(NATIVE_FILE_DROP_EVENT, handle);
    return () => {
      window.removeEventListener(NATIVE_FILE_DROP_EVENT, handle);
      clearTimer();
      pendingRef.current = null;
      awaitingHandlerRef.current = null;
    };
  }, [clearTimer]);

  const isFileDrag = (event: ReactDragEvent<HTMLElement>) =>
    enabled && Array.from(event.dataTransfer?.types ?? []).includes("Files");

  const handlers = {
    onDragEnter(event: ReactDragEvent<HTMLElement>) {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      if (depthRef.current === 0) pendingRef.current = null;
      depthRef.current += 1;
      setActive(true);
    },
    onDragOver(event: ReactDragEvent<HTMLElement>) {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    },
    onDragLeave(event: ReactDragEvent<HTMLElement>) {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      depthRef.current = Math.max(0, depthRef.current - 1);
      if (depthRef.current === 0) {
        setActive(false);
        pendingRef.current = null;
      }
    },
    onDrop(event: ReactDragEvent<HTMLElement>) {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      // 不要 stopPropagation：pywebview 依赖 document 上的原生 drop 监听拿到路径。
      depthRef.current = 0;
      setActive(false);
      const pending = pendingRef.current;
      pendingRef.current = null;
      awaitingRef.current = false;
      awaitingHandlerRef.current = null;
      clearTimer();
      if (pending) {
        if (pending.length > 0) onFilesRef.current(pending);
        return;
      }
      awaitingRef.current = true;
      // Bind the destination to the DOM drop, even if navigation occurs before
      // the corresponding native event delivers the file capabilities.
      awaitingHandlerRef.current = onFilesRef.current;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        if (!awaitingRef.current) return;
        awaitingRef.current = false;
        awaitingHandlerRef.current = null;
        onUnavailableRef.current();
      }, 900);
    },
  };

  return { active, handlers };
}
