import {
  AlertCircle,
  CheckCircle2,
  Info,
  type LucideIcon,
  X,
} from "lucide-react";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export type ToastKind = "success" | "error" | "info";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastInput {
  id?: string;
  kind: ToastKind;
  message: string;
  duration?: number;
  action?: ToastAction;
}

interface ToastItem extends ToastInput {
  id: string;
}

interface ToastApi {
  showToast: (toast: ToastInput) => string;
  dismissToast: (id: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const toastIcons: Record<ToastKind, LucideIcon> = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const sequence = useRef(0);
  const timers = useRef(new Map<string, number>());

  const dismissToast = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.current.delete(id);
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const showToast = useCallback(
    (input: ToastInput) => {
      const id = input.id ?? `toast-${++sequence.current}`;
      const previousTimer = timers.current.get(id);
      if (previousTimer !== undefined) window.clearTimeout(previousTimer);
      timers.current.delete(id);
      setToasts((current) => {
        const next = { ...input, id };
        if (current.some((toast) => toast.id === id)) {
          return current.map((toast) => (toast.id === id ? next : toast));
        }
        return [...current, next];
      });
      const duration = input.duration ?? (input.kind === "error" ? 0 : 4500);
      if (duration > 0) {
        timers.current.set(
          id,
          window.setTimeout(() => dismissToast(id), duration),
        );
      }
      return id;
    },
    [dismissToast],
  );

  useEffect(
    () => () => {
      for (const timer of timers.current.values()) window.clearTimeout(timer);
      timers.current.clear();
    },
    [],
  );

  const value = useMemo(
    () => ({ showToast, dismissToast }),
    [dismissToast, showToast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="toast-viewport"
        aria-label="操作通知"
        aria-live="polite"
        aria-atomic="false"
      >
        {toasts.map((toast) => {
          const Icon = toastIcons[toast.kind];
          return (
            <div
              className={`toast toast-${toast.kind}`}
              key={toast.id}
              role={toast.kind === "error" ? "alert" : "status"}
            >
              <Icon className="toast-icon" aria-hidden="true" />
              <p>{toast.message}</p>
              {toast.action && (
                <button
                  type="button"
                  className="toast-action"
                  onClick={() => {
                    toast.action?.onClick();
                    dismissToast(toast.id);
                  }}
                >
                  {toast.action.label}
                </button>
              )}
              <button
                type="button"
                className="toast-close"
                aria-label="关闭通知"
                onClick={() => dismissToast(toast.id)}
              >
                <X aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast 必须在 ToastProvider 内使用");
  return context;
}
