import { ExternalLink, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { PanFilePicker } from "@/components/PanFilePicker";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import {
  getAssignmentDetail,
  getAssignments,
  openExternalAssignment,
  pickAssignmentLocalFile,
  submitAssignmentCloudFile,
  submitAssignmentLocalFile,
  submitAssignmentText,
  submitAssignmentUrl,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type {
  AssignmentCategory,
  AssignmentItem,
  NativeSubmissionType,
  PanItem,
  PickedLocalFile,
  SubmissionResult,
} from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

const filters: Array<{ value: AssignmentCategory; label: string }> = [
  { value: "today", label: "今天" },
  { value: "upcoming", label: "即将截止" },
  { value: "overdue", label: "已逾期" },
  { value: "missing", label: "缺交" },
  { value: "unsubmitted", label: "未提交" },
  { value: "submitted", label: "已提交" },
  { value: "pending_review", label: "待批改" },
  { value: "graded", label: "已评分" },
];

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
  if (item.submission?.late) return { label: "已逾期", tone: "danger" };
  if (item.categories.includes("today")) {
    return { label: "今天截止", tone: "warning" };
  }
  return getWorkflowMeta(item.submission?.workflow_state);
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
      <strong>提交已由 Canvas 验证</strong>
      <span>提交 ID：{result.submission_id ?? "—"}</span>
      <span>状态：{getWorkflowMeta(result.workflow_state).label}</span>
      <span>时间：{formatDateTime(result.submitted_at)}</span>
      <span>尝试次数：{result.attempt ?? "—"}</span>
      {result.attachments.map((item) => (
        <span key={String(item.id)}>文件：{item.name || item.id}</span>
      ))}
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

  useModalFocus(dialogRef, onCancel, {
    initialFocusRef: cancelRef,
    dismissible: !submitting,
  });

  return (
    <div className="confirm-layer" data-modal-layer>
      <button
        type="button"
        className="confirm-backdrop"
        aria-label="取消提交"
        disabled={submitting}
        onClick={onCancel}
      />
      <section
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
        tabIndex={-1}
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
          <Button type="button" disabled={submitting} onClick={onConfirm}>
            {submitting
              ? "提交并验证中…"
              : submissionError
                ? "重新提交"
                : "确认提交"}
          </Button>
        </div>
      </section>
    </div>
  );
}

export function AssignmentsView() {
  const [category, setCategory] = useState<AssignmentCategory>("today");
  const [items, setItems] = useState<AssignmentItem[] | null>(null);
  const [selected, setSelected] = useState<AssignmentItem | null>(null);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [localFile, setLocalFile] = useState<PickedLocalFile | null>(null);
  const [showPan, setShowPan] = useState(false);
  const [pending, setPending] = useState<PendingSubmission | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submissionError, setSubmissionError] = useState("");
  const [result, setResult] = useState<SubmissionResult | null>(null);
  const categoryRef = useRef(category);
  categoryRef.current = category;
  const { showToast } = useToast();

  const load = useCallback(async () => {
    setItems(null);
    setError("");
    setSelected(null);
    try {
      setItems((await getAssignments(category)).items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "作业读取失败");
    }
  }, [category]);
  useEffect(() => void load(), [load]);

  const selectAssignment = async (item: AssignmentItem) => {
    setError("");
    setResult(null);
    try {
      setSelected(
        await getAssignmentDetail(Number(item.course_id), Number(item.id)),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "作业详情读取失败");
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
      showToast({
        kind: "error",
        message,
      });
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

  const openExternal = async () => {
    if (!selected) return;
    try {
      await openExternalAssignment(
        Number(selected.course_id),
        Number(selected.id),
      );
      showToast({
        kind: "success",
        message: "已在浏览器打开 Canvas 作业页面。",
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
    <div className="section-stack assignments-page">
      <div className="view-intro">
        <div>
          <h2>Assignment Center</h2>
          <p>查看 Canvas 实时状态并安全提交作业。</p>
        </div>
      </div>
      <div className="assignment-filters" role="tablist" aria-label="作业分类">
        {filters.map((filter) => (
          <button
            key={filter.value}
            type="button"
            role="tab"
            aria-selected={category === filter.value}
            className={
              category === filter.value ? "assignment-filter-active" : ""
            }
            onClick={() => setCategory(filter.value)}
          >
            {filter.label}
          </button>
        ))}
      </div>
      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : items === null ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState
          title="此分类暂无作业"
          description="Canvas 没有返回符合条件的作业。"
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
                    selected?.id === item.id
                      ? "assignment-card assignment-card-active"
                      : "assignment-card"
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
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void openExternal()}
                  >
                    <ExternalLink aria-hidden="true" />在 Canvas 打开
                  </Button>
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
                </dl>
                {selected.description && (
                  <p className="assignment-description">
                    {selected.description}
                  </p>
                )}
                {selected.requires_external_submission && (
                  <div className="notice">
                    此作业需要在 Canvas 外部页面完成提交。
                  </div>
                )}
                {selected.can_submit && (
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
                        <label htmlFor="assignment-text">文本内容</label>
                        <textarea
                          id="assignment-text"
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
                        <label htmlFor="assignment-url">作业网址</label>
                        <input
                          id="assignment-url"
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
                      <div className="file-actions">
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
                )}
                {result?.verified && <VerificationDetails result={result} />}
              </>
            )}
          </section>
        </div>
      )}
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
    </div>
  );
}
