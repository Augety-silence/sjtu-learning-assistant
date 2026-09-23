import type { ReactNode } from "react";
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

export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry: () => void;
}) {
  return (
    <div className="state-box state-stack" role="alert">
      <p className="font-medium text-danger">加载失败</p>
      <p className="text-sm text-caption">{message}</p>
      <Button variant="outline" size="sm" onClick={retry}>
        重试
      </Button>
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
