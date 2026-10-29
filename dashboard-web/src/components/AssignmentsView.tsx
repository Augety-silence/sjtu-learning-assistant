import {
  ExternalLink,
  FileText,
  Link2,
  ShieldCheck,
  Upload,
} from "lucide-react";
import {
  AnimatePresence,
  motion,
  useIsPresent,
  useReducedMotion,
} from "motion/react";
import {
  type DragEvent as ReactDragEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import assignmentSubmittedIllustration from "@/assets/empty-states/assignment-submitted.webp";
import { ContentReveal } from "@/components/ContentReveal";
import { PanFilePicker } from "@/components/PanFilePicker";
import { RichPreviewContent } from "@/components/RichPreviewContent";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import { MetaTag } from "@/components/ui/CompactControls";
import {
  SelectionIndicator,
  useSelectionIndicatorId,
} from "@/components/ui/SelectionIndicator";
import {
  getAssignmentDetail,
  getAssignments,
  openExternalAssignment,
  pickAssignmentLocalFile,
  registerAssignmentLocalFile,
  submitAssignmentCloudFile,
  submitAssignmentLocalFile,
  submitAssignmentText,
  submitAssignmentUrl,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { motionDuration, motionEase } from "@/lib/motion";
import type {
  AssignmentCategory,
  AssignmentItem,
  NativeSubmissionType,
  PanItem,
  PickedLocalFile,
  SubmissionResult,
} from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";
import "./assignments-workspace.css";

export interface AssignmentTarget {
  courseId: number;
  assignmentId: number;
}

interface AssignmentsViewProps {
  initialTarget?: AssignmentTarget | null;
  onInitialTargetHandled?: () => void;
}

const filters: Array<{ value: AssignmentCategory; label: string }> = [
  { value: "all", label: "全部" },
  { value: "today", label: "今天" },
  { value: "upcoming", label: "即将截止" },
  { value: "overdue", label: "已逾期" },
  { value: "missing", label: "缺交" },
  { value: "unsubmitted", label: "未提交" },
  { value: "submitted", label: "已提交" },
  { value: "pending_review", label: "待批改" },
  { value: "graded", label: "已评分" },
];

const NATIVE_FILE_DROP_EVENT = "sjtu-native-file-drop";

const typeLabels: Record<string, string> = {
  online_text_entry: "文本提交",
  online_url: "网址提交",
  online_upload: "文件提交",
  external_tool: "外部工具",
  none: "无在线提交",
};

type AssignmentTone = "danger" | "warning" | "success" | "info" | "neutral";

type AssignmentStatusMeta = {
  label: string;
  tone: AssignmentTone;
};

const workflowMeta: Record<string, AssignmentStatusMeta> = {
  submitted: { label: "已提交", tone: "success" },
  pending_review: { label: "待批改", tone: "info" },
  graded: { label: "已评分", tone: "info" },
  unsubmitted: { label: "未提交", tone: "neutral" },
};

function getWorkflowMeta(
  workflowState: string | null | undefined,
): AssignmentStatusMeta {
  const state = workflowState || "unsubmitted";
  return workflowMeta[state] ?? { label: state, tone: "neutral" };
}

function getAssignmentStatus(item: AssignmentItem): AssignmentStatusMeta {
  if (item.submission?.missing) return { label: "缺交", tone: "danger" };
  // 本地缓存路径的 submission 不带 late/missing，逾期只体现在后端计算的 categories 中。
  if (item.submission?.late || item.categories.includes("overdue")) {
    return { label: "已逾期", tone: "danger" };
  }
  const workflow = getWorkflowMeta(item.submission?.workflow_state);
  // 今天截止但已提交/待批改/已评分的作业应显示真实提交状态，而不是继续警示。
  if (item.categories.includes("today") && workflow.tone === "neutral") {
    return { label: "今天截止", tone: "warning" };
  }
  return workflow;
}

type PendingSubmission = {
  type: NativeSubmissionType;
  label: string;
  run: () => Promise<SubmissionResult>;
};

function withVerifiedSubmission(
  item: AssignmentItem,
  result: SubmissionResult,
): AssignmentItem {
  const workflowState = result.workflow_state || "submitted";
  return {
    ...item,
    submission: {
      id: result.submission_id,
      workflow_state: workflowState,
      submission_type: result.submission_type,
      submitted_at: result.submitted_at,
      attempt: result.attempt,
      missing: false,
      late: item.submission?.late ?? false,
      score: item.submission?.score ?? null,
      grade: item.submission?.grade ?? null,
      attachments: result.attachments,
    },
    can_submit: false,
  };
}

function sameAssignment(left: AssignmentItem, right: AssignmentItem) {
  return left.course_id === right.course_id && left.id === right.id;
}

function VerificationDetails({ result }: { result: SubmissionResult }) {
  return (
    <div className="submission-success" role="status">
      <img
        className="submission-success-illustration"
        src={assignmentSubmittedIllustration}
        alt=""
        aria-hidden="true"
      />
      <div className="submission-success-content">
        <strong>提交已由 Canvas 验证</strong>
        <span>提交 ID：{result.submission_id ?? "—"}</span>
        <span>状态：{getWorkflowMeta(result.workflow_state).label}</span>
        <span>时间：{formatDateTime(result.submitted_at)}</span>
        <span>尝试次数：{result.attempt ?? "—"}</span>
        {result.attachments.map((item) => (
          <span key={String(item.id)}>文件：{item.name || item.id}</span>
        ))}
      </div>
    </div>
  );
}

function SubmissionConfirmDialog({
  assignment,
  pending,
  submitting,
  submissionError,
  onCancel,
  onConfirm,
}: {
  assignment: AssignmentItem;
  pending: PendingSubmission;
  submitting: boolean;
  submissionError: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const isPresent = useIsPresent();
  const reduceMotion = useReducedMotion();
  // The backend raises these when Canvas may already hold this attempt
  // (SubmissionOutcomeUncertain / SubmissionVerificationError); a one-click
  // "重新提交" here would create a duplicate attempt the message warns against.
  const outcomeUncertain = /重复提交/.test(submissionError);

  useModalFocus(dialogRef, onCancel, {
    initialFocusRef: cancelRef,
    dismissible: !submitting,
    active: isPresent,
  });

  return createPortal(
    <div
      className="confirm-layer"
      data-modal-layer
      data-motion-layer="modal"
      aria-hidden={isPresent ? undefined : true}
    >
      <motion.button
        type="button"
        className="confirm-backdrop"
        initial={reduceMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{
          type: "tween",
          duration: reduceMotion
            ? 0
            : isPresent
              ? motionDuration.control
              : motionDuration.fast,
        }}
        aria-label="取消提交"
        disabled={submitting || !isPresent}
        onClick={onCancel}
      />
      <motion.section
        ref={dialogRef}
        className="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby={
          submissionError
            ? "confirm-description confirm-error"
            : "confirm-description"
        }
        aria-busy={submitting}
        aria-hidden={isPresent ? undefined : true}
        data-motion-surface="modal"
        tabIndex={-1}
        initial={reduceMotion ? false : { opacity: 0.55 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{
          type: "tween",
          duration: reduceMotion
            ? 0
            : isPresent
              ? motionDuration.overlayEnter
              : motionDuration.fast,
          ease: motionEase.out,
        }}
      >
        <h3 id="confirm-title">确认提交作业？</h3>
        <div id="confirm-description">
          <p>课程：{assignment.course_name}</p>
          <p>作业：{assignment.name}</p>
          <p>类型：{typeLabels[pending.type]}</p>
          <p>内容 / 文件：{pending.label}</p>
        </div>
        {submissionError && (
          <p id="confirm-error" className="submission-error" role="alert">
            {submissionError}
          </p>
        )}
        <div className="confirm-actions">
          <Button
            ref={cancelRef}
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onCancel}
          >
            取消
          </Button>
          <Button
            type="button"
            disabled={submitting || outcomeUncertain}
            onClick={onConfirm}
          >
            {submitting
              ? "提交并验证中…"
              : outcomeUncertain
                ? "请先在 Canvas 核对"
                : submissionError
                  ? "重新提交"
                  : "确认提交"}
          </Button>
        </div>
      </motion.section>
    </div>,
    document.body,
  );
}

export function AssignmentsView({
  initialTarget,
  onInitialTargetHandled,
}: AssignmentsViewProps = {}) {
  const [category, setCategory] = useState<AssignmentCategory>(
    initialTarget ? "upcoming" : "all",
  );
  const [items, setItems] = useState<AssignmentItem[] | null>(null);
  const [selected, setSelected] = useState<AssignmentItem | null>(null);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [localFile, setLocalFile] = useState<PickedLocalFile | null>(null);
  const [fileDragActive, setFileDragActive] = useState(false);
  const [showPan, setShowPan] = useState(false);
  const [pending, setPending] = useState<PendingSubmission | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submissionError, setSubmissionError] = useState("");
  const [result, setResult] = useState<SubmissionResult | null>(null);
  const filterIndicatorId = useSelectionIndicatorId("assignment-filter");
  const categoryRef = useRef(category);
  const handledTargetRef = useRef("");
  const listRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const fileDragDepthRef = useRef(0);
  const awaitingNativeDropRef = useRef(false);
  const pendingNativeDropPathsRef = useRef<string[] | null>(null);
  const nativeDropTimerRef = useRef<number | null>(null);
  const droppedFileRequestRef = useRef(0);
  const selectedRef = useRef(selected);
  const onInitialTargetHandledRef = useRef(onInitialTargetHandled);
  categoryRef.current = category;
  selectedRef.current = selected;
  onInitialTargetHandledRef.current = onInitialTargetHandled;
  const { showToast } = useToast();

  const load = useCallback(async () => {
    const request = ++listRequestRef.current;
    setItems(null);
    setError("");
    try {
      const next = (await getAssignments(category)).items;
      if (listRequestRef.current === request) setItems(next);
    } catch (reason) {
      if (listRequestRef.current === request) {
        setError(reason instanceof Error ? reason.message : "作业读取失败");
      }
    }
  }, [category]);
  useEffect(() => void load(), [load]);

  useEffect(() => {
    if (!initialTarget) return;
    const targetKey = `${initialTarget.courseId}:${initialTarget.assignmentId}`;
    if (handledTargetRef.current === targetKey) return;
    handledTargetRef.current = targetKey;
    let active = true;
    let settled = false;
    setError("");
    setResult(null);
    void getAssignmentDetail(initialTarget.courseId, initialTarget.assignmentId)
      .then((detail) => {
        if (!active) return;
        droppedFileRequestRef.current += 1;
        setSelected(detail);
        setItems((current) => {
          if (
            !current ||
            current.some((item) => sameAssignment(item, detail))
          ) {
            return current;
          }
          return [detail, ...current];
        });
        const preferredCategory = filters.find((filter) =>
          detail.categories.includes(filter.value),
        )?.value;
        if (preferredCategory && preferredCategory !== categoryRef.current) {
          setCategory(preferredCategory);
        }
      })
      .catch((reason) => {
        if (active) {
          setError(
            reason instanceof Error ? reason.message : "作业详情读取失败",
          );
        }
      })
      .finally(() => {
        settled = true;
        if (active) onInitialTargetHandledRef.current?.();
      });
    return () => {
      active = false;
      // Cancelled before the detail arrived (StrictMode replay or a parent re-render):
      // release the key so the next run fetches again instead of silently dropping the target.
      if (!settled && handledTargetRef.current === targetKey) {
        handledTargetRef.current = "";
      }
    };
  }, [initialTarget]);

  const selectAssignment = async (item: AssignmentItem) => {
    droppedFileRequestRef.current += 1;
    const request = ++detailRequestRef.current;
    setError("");
    setResult(null);
    if (!selected || !sameAssignment(selected, item)) {
      setLocalFile(null);
      setShowPan(false);
    }
    try {
      const detail = await getAssignmentDetail(
        Number(item.course_id),
        Number(item.id),
      );
      if (detailRequestRef.current === request) setSelected(detail);
    } catch (reason) {
      if (detailRequestRef.current === request) {
        setError(reason instanceof Error ? reason.message : "作业详情读取失败");
      }
    }
  };

  const queue = (
    type: NativeSubmissionType,
    label: string,
    run: () => Promise<SubmissionResult>,
  ) => {
    setSubmissionError("");
    setPending({ type, label, run });
  };

  const submit = async () => {
    if (!pending || !selected) return;
    const submittedAssignment = selected;
    const submittedCategory = category;
    setSubmitting(true);
    setSubmissionError("");
    setResult(null);
    try {
      const next = await pending.run();
      if (!next.verified) {
        throw new Error(
          next.message || "Canvas 尚未验证本次提交，不能标记为成功。",
        );
      }
      const optimistic = withVerifiedSubmission(submittedAssignment, next);
      setResult(next);
      setPending(null);
      setSelected((current) =>
        current && sameAssignment(current, submittedAssignment)
          ? optimistic
          : current,
      );
      setItems(
        (current) =>
          current?.map((item) =>
            sameAssignment(item, submittedAssignment) ? optimistic : item,
          ) ?? current,
      );
      setText("");
      setUrl("");
      setLocalFile(null);
      setShowPan(false);
      showToast({ kind: "success", message: "Canvas 已验证提交成功。" });
      void Promise.allSettled([
        getAssignmentDetail(
          Number(submittedAssignment.course_id),
          Number(submittedAssignment.id),
        ),
        getAssignments(submittedCategory),
      ]).then(([detail, list]) => {
        if (detail.status === "fulfilled") {
          setSelected((current) =>
            current && sameAssignment(current, submittedAssignment)
              ? detail.value
              : current,
          );
        }
        if (
          list.status === "fulfilled" &&
          categoryRef.current === submittedCategory
        ) {
          setItems(list.value.items);
        }
      });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "提交失败";
      setSubmissionError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const chooseLocal = async () => {
    try {
      const picked = await pickAssignmentLocalFile();
      if (!picked.cancelled) setLocalFile(picked);
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "文件选择失败",
      });
    }
  };

  const registerDroppedPaths = useCallback(
    async (paths: string[]) => {
      if (paths.length === 0) return;
      const request = ++droppedFileRequestRef.current;
      const assignmentKey = selectedRef.current
        ? `${selectedRef.current.course_id}:${selectedRef.current.id}`
        : null;
      const isCurrentRequest = () =>
        droppedFileRequestRef.current === request &&
        (selectedRef.current
          ? `${selectedRef.current.course_id}:${selectedRef.current.id}`
          : null) === assignmentKey;
      if (paths.length > 1) {
        showToast({
          kind: "info",
          message: "一次只能提交一个文件，已使用拖入的第一个文件。",
        });
      }
      try {
        const registered = await registerAssignmentLocalFile(paths[0]);
        if (isCurrentRequest() && !registered.cancelled)
          setLocalFile(registered);
      } catch (reason) {
        if (!isCurrentRequest()) return;
        showToast({
          kind: "error",
          message:
            reason instanceof Error ? reason.message : "拖入文件登记失败",
        });
      }
    },
    [showToast],
  );

  useEffect(() => {
    const handleNativeDrop = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (
        typeof detail !== "object" ||
        detail === null ||
        !("paths" in detail) ||
        !Array.isArray(detail.paths) ||
        detail.paths.some((path) => typeof path !== "string")
      )
        return;
      if (!awaitingNativeDropRef.current) {
        if (fileDragDepthRef.current > 0)
          pendingNativeDropPathsRef.current = detail.paths;
        return;
      }
      awaitingNativeDropRef.current = false;
      if (nativeDropTimerRef.current !== null) {
        window.clearTimeout(nativeDropTimerRef.current);
        nativeDropTimerRef.current = null;
      }
      void registerDroppedPaths(detail.paths);
    };
    window.addEventListener(NATIVE_FILE_DROP_EVENT, handleNativeDrop);
    return () => {
      window.removeEventListener(NATIVE_FILE_DROP_EVENT, handleNativeDrop);
      if (nativeDropTimerRef.current !== null)
        window.clearTimeout(nativeDropTimerRef.current);
      pendingNativeDropPathsRef.current = null;
    };
  }, [registerDroppedPaths]);

  const isFileDrag = (event: ReactDragEvent<HTMLElement>) =>
    Array.from(event.dataTransfer.types).includes("Files");

  const handleFileDragEnter = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    if (fileDragDepthRef.current === 0)
      pendingNativeDropPathsRef.current = null;
    fileDragDepthRef.current += 1;
    setFileDragActive(true);
  };

  const handleFileDragOver = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  const handleFileDragLeave = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    fileDragDepthRef.current = Math.max(0, fileDragDepthRef.current - 1);
    if (fileDragDepthRef.current === 0) setFileDragActive(false);
    if (fileDragDepthRef.current === 0)
      pendingNativeDropPathsRef.current = null;
  };

  const handleFileDrop = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    // Do not stopPropagation(): React 17+ handles this at the root container, so
    // stopping here keeps the native drop from reaching pywebview's `document`
    // drop listener, which is the only source of file paths on WebView2/GTK/Qt.
    fileDragDepthRef.current = 0;
    const pendingNativePaths = pendingNativeDropPathsRef.current;
    pendingNativeDropPathsRef.current = null;
    setFileDragActive(false);
    // A new WebKit drop supersedes any unresolved native fallback from the previous gesture.
    awaitingNativeDropRef.current = false;
    if (nativeDropTimerRef.current !== null) {
      window.clearTimeout(nativeDropTimerRef.current);
      nativeDropTimerRef.current = null;
    }
    const paths = Array.from(event.dataTransfer.files).flatMap((file) => {
      const path = (file as File & { path?: string }).path;
      return path ? [path] : [];
    });
    if (paths.length > 0) {
      void registerDroppedPaths(paths);
      return;
    }
    if (pendingNativePaths) {
      void registerDroppedPaths(pendingNativePaths);
      return;
    }
    awaitingNativeDropRef.current = true;
    nativeDropTimerRef.current = window.setTimeout(() => {
      nativeDropTimerRef.current = null;
      if (!awaitingNativeDropRef.current) return;
      awaitingNativeDropRef.current = false;
      showToast({
        kind: "error",
        message: "无法读取拖入文件的本地路径，请改用“选择本地文件”。",
      });
    }, 900);
  };

  const openExternal = async () => {
    if (!selected) return;
    try {
      await openExternalAssignment(
        Number(selected.course_id),
        Number(selected.id),
      );
      showToast({
        kind: "success",
        message: "已打开该作业的外部工具页面。",
      });
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "打开失败",
      });
    }
  };

  const hasType = (type: string) => selected?.submission_types.includes(type);
  return (
    <ContentReveal
      className="section-stack assignments-page"
      ready={items !== null || Boolean(error)}
    >
      <div className="view-intro">
        <div>
          <h2>作业工作台</h2>
          <p>阅读要求、准备答案，并在本机完成提交与 Canvas 回读验证。</p>
        </div>
      </div>
      <motion.div
        layoutScroll
        className="assignment-filters"
        role="tablist"
        aria-label="作业分类"
        onKeyDown={(event) => {
          // Tabs keyboard pattern: one Tab stop (roving tabindex), arrows wrap, Home/End jump.
          // Leave modified chords (Alt+← back, Ctrl+Home, …) to the browser/WebView2.
          if (event.altKey || event.ctrlKey || event.metaKey) return;
          const current = filters.findIndex((item) => item.value === category);
          const last = filters.length - 1;
          const next =
            event.key === "ArrowRight"
              ? current >= last
                ? 0
                : current + 1
              : event.key === "ArrowLeft"
                ? current <= 0
                  ? last
                  : current - 1
                : event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? last
                    : -1;
          if (next < 0) return;
          event.preventDefault();
          if (next !== current) {
            droppedFileRequestRef.current += 1;
            setSelected(null);
            setCategory(filters[next].value);
          }
          event.currentTarget
            .querySelectorAll<HTMLButtonElement>('[role="tab"]')
            [next]?.focus();
        }}
      >
        {filters.map((filter) => (
          <button
            key={filter.value}
            type="button"
            data-selection-layout-id={filterIndicatorId}
            role="tab"
            aria-selected={category === filter.value}
            tabIndex={category === filter.value ? 0 : -1}
            className={
              category === filter.value
                ? "selection-host selection-active assignment-filter-active"
                : "selection-host"
            }
            onClick={() => {
              droppedFileRequestRef.current += 1;
              setSelected(null);
              setCategory(filter.value);
            }}
          >
            {category === filter.value && (
              <SelectionIndicator
                layoutId={filterIndicatorId}
                radius={9}
                className="assignment-filter-indicator"
              />
            )}
            <span className="selection-label">{filter.label}</span>
          </button>
        ))}
      </motion.div>
      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : items === null ? (
        <LoadingState />
      ) : items.length === 0 && !selected ? (
        <EmptyState
          title={category === "all" ? "暂无作业" : "此分类暂无作业"}
          description={
            category === "all"
              ? "当前已同步的范围内没有作业；如刚完成登录，可先同步数据后再查看。"
              : "Canvas 没有返回符合条件的作业。"
          }
        />
      ) : (
        <div className="assignment-layout">
          <div className="assignment-list" aria-label="作业列表">
            {items.map((item) => {
              const status = getAssignmentStatus(item);
              return (
                <button
                  key={`${item.course_id}:${item.id}`}
                  type="button"
                  className={
                    selected && sameAssignment(selected, item)
                      ? "assignment-card assignment-card-active"
                      : "assignment-card"
                  }
                  aria-current={
                    selected && sameAssignment(selected, item)
                      ? "true"
                      : undefined
                  }
                  data-tone={status.tone}
                  onClick={() => void selectAssignment(item)}
                >
                  <strong>{item.name}</strong>
                  <span>{item.course_name}</span>
                  <span className="assignment-card-footer">
                    <small>{formatDateTime(item.due_at)}</small>
                    <span
                      className={`assignment-status assignment-status-${status.tone}`}
                    >
                      {status.label}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
          <section className="assignment-detail" aria-live="polite">
            {!selected ? (
              <p className="finder-empty">选择一项作业查看详情</p>
            ) : (
              <>
                <div className="assignment-detail-heading">
                  <div>
                    <span>{selected.course_name}</span>
                    <h3>{selected.name}</h3>
                  </div>
                  {!selected.requires_external_submission ? (
                    <MetaTag
                      className="assignment-native-badge"
                      icon={ShieldCheck}
                      tone="info"
                    >
                      App 内提交
                    </MetaTag>
                  ) : null}
                </div>
                <dl className="assignment-meta">
                  <div>
                    <dt>截止时间</dt>
                    <dd>{formatDateTime(selected.due_at)}</dd>
                  </div>
                  <div>
                    <dt>提交状态</dt>
                    <dd>
                      {
                        getWorkflowMeta(selected.submission?.workflow_state)
                          .label
                      }
                    </dd>
                  </div>
                  <div>
                    <dt>提交类型</dt>
                    <dd>
                      {selected.submission_types
                        .map((value) => typeLabels[value] || value)
                        .join("、") || "无"}
                    </dd>
                  </div>
                  <div>
                    <dt>满分</dt>
                    <dd>{selected.points_possible ?? "—"}</dd>
                  </div>
                </dl>
                {selected.description && (
                  <section className="assignment-requirements">
                    <h4>作业要求</h4>
                    <div className="assignment-description">
                      <RichPreviewContent
                        kind="html"
                        title="作业要求"
                        content={selected.description}
                      />
                    </div>
                  </section>
                )}
                {selected.requires_external_submission && (
                  <div className="assignment-external-notice">
                    <div>
                      <strong>此作业由外部工具接管</strong>
                      <p>
                        Canvas 未开放原生提交接口，通常是
                        LTI、在线测验或讨论任务。
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void openExternal()}
                    >
                      <ExternalLink aria-hidden="true" />
                      打开外部工具
                    </Button>
                  </div>
                )}
                {selected.can_submit && (
                  <section
                    className="assignment-submit-workspace"
                    aria-labelledby="assignment-submit-title"
                  >
                    <div className="assignment-submit-heading">
                      <div>
                        <h4 id="assignment-submit-title">在 App 内提交</h4>
                        <p>提交前会再次确认；成功后立即向 Canvas 回读状态。</p>
                      </div>
                      <ShieldCheck aria-hidden="true" />
                    </div>
                    <div className="submission-forms">
                      {hasType("online_text_entry") && (
                        <form
                          onSubmit={(event) => {
                            event.preventDefault();
                            queue("online_text_entry", "文本内容", () =>
                              submitAssignmentText(
                                Number(selected.course_id),
                                Number(selected.id),
                                text,
                              ),
                            );
                          }}
                        >
                          <label htmlFor="assignment-text">
                            <FileText aria-hidden="true" />
                            文本作答
                          </label>
                          <textarea
                            id="assignment-text"
                            aria-label="文本内容"
                            value={text}
                            maxLength={200000}
                            required
                            disabled={submitting}
                            onChange={(event) => setText(event.target.value)}
                          />
                          <Button
                            type="submit"
                            disabled={submitting || !text.trim()}
                          >
                            准备提交文本
                          </Button>
                        </form>
                      )}
                      {hasType("online_url") && (
                        <form
                          onSubmit={(event) => {
                            event.preventDefault();
                            queue("online_url", url, () =>
                              submitAssignmentUrl(
                                Number(selected.course_id),
                                Number(selected.id),
                                url,
                              ),
                            );
                          }}
                        >
                          <label htmlFor="assignment-url">
                            <Link2 aria-hidden="true" />
                            网址作答
                          </label>
                          <input
                            id="assignment-url"
                            aria-label="作业网址"
                            type="url"
                            value={url}
                            required
                            disabled={submitting}
                            onChange={(event) => setUrl(event.target.value)}
                          />
                          <Button type="submit" disabled={submitting || !url}>
                            准备提交网址
                          </Button>
                        </form>
                      )}
                      {hasType("online_upload") && (
                        <div
                          className={
                            fileDragActive
                              ? "file-actions assignment-file-dropzone assignment-file-dropzone-active"
                              : "file-actions assignment-file-dropzone"
                          }
                          onDragEnter={handleFileDragEnter}
                          onDragOver={handleFileDragOver}
                          onDragLeave={handleFileDragLeave}
                          onDrop={handleFileDrop}
                        >
                          <div className="assignment-method-title">
                            <Upload aria-hidden="true" />
                            <strong>文件作答</strong>
                          </div>
                          <p className="assignment-file-drop-hint">
                            {fileDragActive
                              ? "松开即可添加文件"
                              : "可将本地文件拖到此处"}
                          </p>
                          <Button
                            type="button"
                            variant="outline"
                            disabled={submitting}
                            onClick={() => void chooseLocal()}
                          >
                            <Upload aria-hidden="true" />
                            选择本地文件
                          </Button>
                          {localFile?.path && (
                            <>
                              <span>{localFile.name}</span>
                              <Button
                                type="button"
                                disabled={submitting}
                                onClick={() =>
                                  queue(
                                    "online_upload",
                                    localFile.name || "本地文件",
                                    () =>
                                      submitAssignmentLocalFile(
                                        Number(selected.course_id),
                                        Number(selected.id),
                                        localFile.path as string,
                                      ),
                                  )
                                }
                              >
                                准备提交本地文件
                              </Button>
                            </>
                          )}
                          <Button
                            type="button"
                            variant="outline"
                            disabled={submitting}
                            onClick={() => setShowPan((value) => !value)}
                          >
                            从交大云盘选择
                          </Button>
                          {showPan && (
                            <PanFilePicker
                              disabled={submitting}
                              onSelect={(item: PanItem) =>
                                queue("online_upload", item.name, () =>
                                  submitAssignmentCloudFile(
                                    Number(selected.course_id),
                                    Number(selected.id),
                                    item.remote_path,
                                  ),
                                )
                              }
                            />
                          )}
                        </div>
                      )}
                    </div>
                  </section>
                )}
                {result?.verified && <VerificationDetails result={result} />}
              </>
            )}
          </section>
        </div>
      )}
      <AnimatePresence initial={false}>
        {pending && selected && (
          <SubmissionConfirmDialog
            assignment={selected}
            pending={pending}
            submitting={submitting}
            submissionError={submissionError}
            onCancel={() => {
              setSubmissionError("");
              setPending(null);
            }}
            onConfirm={() => void submit()}
          />
        )}
      </AnimatePresence>
    </ContentReveal>
  );
}
