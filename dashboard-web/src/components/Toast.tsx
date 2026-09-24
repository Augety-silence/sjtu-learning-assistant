import {
  AlertCircle,
  CheckCircle2,
  Info,
  type LucideIcon,
  X,
} from "lucide-react";
import { AnimatePresence, motion, useIsPresent } from "motion/react";
import {
  type CSSProperties,
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
  durationMs: number;
}

interface ToastTimer {
  timerId: number | null;
  remaining: number;
  startedAt: number;
  paused: boolean;
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

function ToastCard({
  toast,
  dismiss,
  pause,
  resume,
}: {
  toast: ToastItem;
  dismiss: () => void;
  pause: () => void;
  resume: () => void;
}) {
  const isPresent = useIsPresent();
  const [hovered, setHovered] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const paused = hovered || focusWithin;
  const Icon = toastIcons[toast.kind];

  useEffect(() => {
    if (paused) pause();
    else resume();
  }, [pause, paused, resume, toast.durationMs]);
  return (
    <motion.div
      className={`toast toast-${toast.kind}`}
      role={
        isPresent ? (toast.kind === "error" ? "alert" : "status") : undefined
      }
      aria-hidden={!isPresent}
      initial={{ opacity: 0.01, x: 10 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 10 }}
      transition={{
        duration: isPresent ? 0.18 : 0.14,
        ease: [0.16, 1, 0.3, 1],
      }}
      layout="position"
      data-paused={paused || undefined}
      style={{ "--toast-duration": `${toast.durationMs}ms` } as CSSProperties}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocusWithin(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          setFocusWithin(false);
        }
      }}
    >
      <Icon className="toast-icon" aria-hidden="true" />
      <p>{toast.message}</p>
      <div className="toast-controls">
        {toast.action && (
          <button
            type="button"
            className="toast-action"
            onClick={() => {
              toast.action?.onClick();
              dismiss();
            }}
          >
            {toast.action.label}
          </button>
        )}
        <button
          type="button"
          className="toast-close"
          aria-label="关闭通知"
          onClick={dismiss}
        >
          <X aria-hidden="true" />
        </button>
      </div>
      {toast.durationMs > 0 && (
        <span className="toast-progress" aria-hidden="true" />
      )}
    </motion.div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const sequence = useRef(0);
  const timers = useRef(new Map<string, ToastTimer>());

  const dismissToast = useCallback((id: string) => {
    const runtime = timers.current.get(id);
    if (runtime?.timerId !== null && runtime?.timerId !== undefined) {
      window.clearTimeout(runtime.timerId);
    }
    timers.current.delete(id);
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const startTimer = useCallback(
    (id: string, remaining: number) => {
      const timerId = window.setTimeout(() => dismissToast(id), remaining);
      timers.current.set(id, {
        timerId,
        remaining,
        startedAt: Date.now(),
        paused: false,
      });
    },
    [dismissToast],
  );

  const pauseToast = useCallback((id: string) => {
    const runtime = timers.current.get(id);
    if (!runtime || runtime.paused) return;
    if (runtime.timerId !== null) window.clearTimeout(runtime.timerId);
    timers.current.set(id, {
      timerId: null,
      remaining: Math.max(
        0,
        runtime.remaining - (Date.now() - runtime.startedAt),
      ),
      startedAt: runtime.startedAt,
      paused: true,
    });
  }, []);

  const resumeToast = useCallback(
    (id: string) => {
      const runtime = timers.current.get(id);
      if (!runtime || !runtime.paused) return;
      if (runtime.remaining <= 0) {
        dismissToast(id);
        return;
      }
      startTimer(id, runtime.remaining);
    },
    [dismissToast, startTimer],
  );

  const showToast = useCallback(
    (input: ToastInput) => {
      const id = input.id ?? `toast-${++sequence.current}`;
      const previousRuntime = timers.current.get(id);
      if (
        previousRuntime?.timerId !== null &&
        previousRuntime?.timerId !== undefined
      ) {
        window.clearTimeout(previousRuntime.timerId);
      }
      timers.current.delete(id);
      const durationMs = input.duration ?? (input.kind === "error" ? 0 : 4500);
      setToasts((current) => {
        const next = { ...input, id, durationMs };
        if (current.some((toast) => toast.id === id)) {
          return current.map((toast) => (toast.id === id ? next : toast));
        }
        return [...current, next];
      });
      if (durationMs > 0) startTimer(id, durationMs);
      return id;
    },
    [startTimer],
  );

  useEffect(
    () => () => {
      for (const runtime of timers.current.values()) {
        if (runtime.timerId !== null) window.clearTimeout(runtime.timerId);
      }
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
        <AnimatePresence initial={false}>
          {toasts.map((toast) => (
            <ToastCard
              key={toast.id}
              toast={toast}
              dismiss={() => dismissToast(toast.id)}
              pause={() => pauseToast(toast.id)}
              resume={() => resumeToast(toast.id)}
            />
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast 必须在 ToastProvider 内使用");
  return context;
}
