import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/Button";

export function LoadingState({ label = "正在加载…" }: { label?: string }) {
  return (
    <div className="state-box" aria-live="polite" aria-busy="true">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
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
    <div className="state-box state-stack" role="status">
      <p className="font-medium text-ink">{title}</p>
      <p className="text-sm text-caption">{description}</p>
      {action}
    </div>
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
    <div className="state-box state-stack state-error" role="alert">
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
    </div>
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
