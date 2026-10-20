import { motion, useReducedMotion } from "motion/react";
import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/Button";
import { motionDuration, motionEase } from "@/lib/motion";

export function LoadingState({ label = "正在加载…" }: { label?: string }) {
  const shouldReduceMotion = useReducedMotion();
  return (
    <motion.div
      className="state-box"
      aria-live="polite"
      aria-busy="true"
      data-motion-surface="state"
      initial={shouldReduceMotion ? false : { opacity: 0.88 }}
      animate={{ opacity: 1 }}
      transition={{ duration: motionDuration.fast, ease: motionEase.out }}
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
  illustration,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  illustration?: ReactNode;
}) {
  const shouldReduceMotion = useReducedMotion();
  return (
    <motion.div
      className="state-box state-stack"
      role="status"
      data-motion-surface="state"
      initial={shouldReduceMotion ? false : { opacity: 0.88 }}
      animate={{ opacity: 1 }}
      transition={{ duration: motionDuration.fast, ease: motionEase.out }}
    >
      {illustration && (
        <div className="state-illustration" aria-hidden="true">
          {illustration}
        </div>
      )}
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
  const shouldReduceMotion = useReducedMotion();
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
      initial={shouldReduceMotion ? false : { opacity: 0.88 }}
      animate={{ opacity: 1 }}
      transition={{ duration: motionDuration.fast, ease: motionEase.out }}
    >
      <p className="font-medium text-[color:var(--status-danger)]">加载失败</p>
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
