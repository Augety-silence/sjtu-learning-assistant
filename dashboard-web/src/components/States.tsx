import { motion } from "motion/react";
import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/Button";

export function LoadingState({ label = "正在加载…" }: { label?: string }) {
  return (
    <motion.div
      className="state-box"
      aria-live="polite"
      aria-busy="true"
      data-motion-surface="state"
      initial={{ opacity: 0.88, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
    >
      <span className="spinner" aria-hidden="true" />
      {label}
    </motion.div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <motion.div
      className="state-box state-stack"
      role="status"
      data-motion-surface="state"
      initial={{ opacity: 0.88, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
    >
      <p className="font-medium text-ink">{title}</p>
      <p className="text-sm text-caption">{description}</p>
      {action}
    </motion.div>
  );
}

export interface StateAction {
  label: string;
  onClick: () => void | Promise<void>;
}

export function ErrorState({
  message,
  retry,
  retryLabel = "重试",
  secondaryAction,
}: {
  message: string;
  retry?: () => void | Promise<void>;
  retryLabel?: string;
  secondaryAction?: StateAction;
}) {
  const [retrying, setRetrying] = useState(false);
  const runRetry = async () => {
    if (!retry || retrying) return;
    setRetrying(true);
    try {
      await retry();
    } finally {
      setRetrying(false);
    }
  };

  return (
    <motion.div
      className="state-box state-stack state-error"
      role="alert"
      data-motion-surface="state"
      initial={{ opacity: 0.88, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
    >
      <p className="font-medium text-danger">加载失败</p>
      <p className="text-sm text-caption">{message}</p>
      <div className="state-actions">
        {retry && (
          <Button
            variant="outline"
            size="sm"
            loading={retrying}
            loadingLabel="正在重试…"
            onClick={() => void runRetry()}
          >
            {retryLabel}
          </Button>
        )}
        {secondaryAction && (
          <Button
            variant="ghost"
            size="sm"
            disabled={retrying}
            onClick={() => void secondaryAction.onClick()}
          >
            {secondaryAction.label}
          </Button>
        )}
      </div>
    </motion.div>
  );
}

export function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="section-header">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}
