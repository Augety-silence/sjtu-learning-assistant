import { Download, Eye } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  RichPreviewContent,
  type RichPreviewContentProps,
} from "@/components/RichPreviewContent";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Section,
} from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";

export interface GradingAttachment {
  id: string;
  name: string;
  size?: number | null;
  downloadStatus?: "ready" | "downloading" | "downloaded" | "failed";
  preview?: RichPreviewContentProps | null;
}

export interface GradingComment {
  id: string;
  author: string;
  content: string;
  createdAt?: string | null;
}

export interface GradingSubmission {
  id: string;
  studentId: string;
  studentName: string;
  loginId?: string | null;
  submittedAt?: string | null;
  status: "submitted" | "late" | "missing" | "graded";
  score?: number | null;
  displayGrade?: string | null;
  attempt?: number | null;
  body?: string | null;
  attachments: GradingAttachment[];
  comments: GradingComment[];
}

export interface GradingReferenceFile {
  id: string;
  name: string;
  status: "ready" | "downloading" | "downloaded" | "cloud_only" | "failed";
}

export interface GradingViewProps {
  assignmentName?: string;
  maxScore?: number | null;
  submissions: GradingSubmission[];
  referenceFiles?: GradingReferenceFile[];
  loading?: boolean;
  error?: string | null;
  permissionDenied?: boolean;
  readOnly?: boolean;
  onRetry?: () => void | Promise<void>;
  onSaveScore?: (input: {
    submissionId: string;
    studentId: string;
    score: number | null;
  }) => void | Promise<void>;
  onAddComment?: (input: {
    submissionId: string;
    studentId: string;
    comment: string;
  }) => void | Promise<void>;
  onDownloadAttachment?: (
    attachment: GradingAttachment,
    submission: GradingSubmission,
  ) => void | Promise<void>;
  onOpenReference?: (file: GradingReferenceFile) => void | Promise<void>;
}

function formatDateTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function statusLabel(status: GradingSubmission["status"]) {
  return {
    submitted: "已提交",
    late: "迟交",
    missing: "未提交",
    graded: "已评分",
  }[status];
}

function referenceLabel(status: GradingReferenceFile["status"]) {
  return {
    ready: "可用",
    downloading: "下载中",
    downloaded: "已下载",
    cloud_only: "仅云端",
    failed: "失败",
  }[status];
}

export function GradingView({
  assignmentName,
  maxScore,
  submissions,
  referenceFiles = [],
  loading = false,
  error,
  permissionDenied = false,
  readOnly = false,
  onRetry,
  onSaveScore,
  onAddComment,
  onDownloadAttachment,
  onOpenReference,
}: GradingViewProps) {
  const [selectedId, setSelectedId] = useState<string | null>(
    () => submissions[0]?.id ?? null,
  );
  const [detailTab, setDetailTab] = useState<
    "submission" | "comments" | "references"
  >("submission");
  const [scoreDraft, setScoreDraft] = useState("");
  const [commentDraft, setCommentDraft] = useState("");
  const [busy, setBusy] = useState<"score" | "comment" | string | null>(null);
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const { showToast } = useToast();
  const selected =
    submissions.find((item) => item.id === selectedId) ??
    submissions[0] ??
    null;
  const pending = submissions.filter(
    (item) => item.status !== "graded" && item.status !== "missing",
  ).length;
  const previewAttachment = selected?.attachments.find(
    (item) => item.preview,
  )?.preview;

  useEffect(() => {
    if (!selected && submissions[0]) setSelectedId(submissions[0].id);
  }, [selected, submissions]);
  useEffect(() => {
    setScoreDraft(
      selected?.score === null || selected?.score === undefined
        ? ""
        : String(selected.score),
    );
    setCommentDraft("");
  }, [selected?.id, selected?.score]);

  const saveScore = async () => {
    if (!selected || !onSaveScore || busy) return;
    const score = scoreDraft.trim() === "" ? null : Number(scoreDraft);
    if (
      score !== null &&
      (!Number.isFinite(score) ||
        score < 0 ||
        (maxScore !== null && maxScore !== undefined && score > maxScore))
    ) {
      showToast({
        kind: "error",
        message: `请输入 0 至 ${maxScore ?? "有效上限"} 的分数。`,
      });
      return;
    }
    setBusy("score");
    try {
      await onSaveScore({
        submissionId: selected.id,
        studentId: selected.studentId,
        score,
      });
      showToast({
        kind: "success",
        message: `已保存 ${selected.studentName} 的评分。`,
      });
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "评分保存失败",
      });
    } finally {
      setBusy(null);
    }
  };

  const addComment = async () => {
    const comment = commentDraft.trim();
    if (!selected || !onAddComment || !comment || busy) return;
    setBusy("comment");
    try {
      await onAddComment({
        submissionId: selected.id,
        studentId: selected.studentId,
        comment,
      });
      setCommentDraft("");
      showToast({ kind: "success", message: "评论已发布。" });
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "评论发布失败",
      });
    } finally {
      setBusy(null);
    }
  };

  const runFileAction = async (
    key: string,
    action: () => void | Promise<void>,
    success: string,
  ) => {
    if (busy) return;
    setBusy(key);
    try {
      await action();
      showToast({ kind: "success", message: success });
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "文件操作失败",
      });
    } finally {
      setBusy(null);
    }
  };

  const selectByOffset = (offset: number) => {
    if (!selected) return;
    const index = submissions.findIndex((item) => item.id === selected.id);
    const next =
      submissions[
        Math.max(0, Math.min(submissions.length - 1, index + offset))
      ];
    if (!next) return;
    setSelectedId(next.id);
    window.setTimeout(() => rowRefs.current.get(next.id)?.focus(), 0);
  };

  const averageScore = useMemo(() => {
    const values = submissions.flatMap((item) =>
      typeof item.score === "number" ? [item.score] : [],
    );
    return values.length
      ? (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(
          1,
        )
      : "—";
  }, [submissions]);

  if (permissionDenied)
    return (
      <EmptyState
        title="没有批改权限"
        description="只有课程教师或助教可以查看提交内容并评分。"
      />
    );
  if (error) return <ErrorState message={error} retry={onRetry} />;
  if (loading) return <LoadingState label="正在加载作业提交…" />;

  return (
    <div className="section-stack assignments-page">
      <div className="view-intro">
        <div>
          <h2>作业批改</h2>
          <p>
            {assignmentName ?? "选择提交后查看详情、评分、评论与参考文件。"}
          </p>
        </div>
      </div>
      <dl className="summary-strip" aria-label="批改概览">
        <div className="summary-item summary-secondary">
          <dt>提交数量</dt>
          <dd>{submissions.length}</dd>
          <span>当前作业</span>
        </div>
        <div className="summary-item summary-urgent">
          <dt>待批改</dt>
          <dd>{pending}</dd>
          <span>需要处理</span>
        </div>
        <div className="summary-item summary-priority">
          <dt>平均分</dt>
          <dd>{averageScore}</dd>
          <span>满分 {maxScore ?? "—"}</span>
        </div>
      </dl>

      {submissions.length === 0 ? (
        <EmptyState
          title="暂无提交"
          description="此作业还没有可供批改的学生提交。"
        />
      ) : (
        <div className="assignment-layout">
          <div className="table-surface" aria-label="提交列表">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>学生</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>分数</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {submissions.map((submission) => (
                  <TableRow
                    key={submission.id}
                    className={
                      submission.id === selected?.id
                        ? "finder-row-selected"
                        : ""
                    }
                  >
                    <TableCell className="overflow-visible">
                      <button
                        ref={(node) => {
                          if (node) rowRefs.current.set(submission.id, node);
                          else rowRefs.current.delete(submission.id);
                        }}
                        type="button"
                        className="table-link text-left font-medium"
                        aria-current={
                          submission.id === selected?.id ? "true" : undefined
                        }
                        onClick={() => setSelectedId(submission.id)}
                        onKeyDown={(event) => {
                          if (
                            event.key === "ArrowDown" ||
                            event.key === "ArrowUp"
                          ) {
                            event.preventDefault();
                            selectByOffset(event.key === "ArrowDown" ? 1 : -1);
                          }
                        }}
                      >
                        {submission.studentName}
                      </button>
                    </TableCell>
                    <TableCell>
                      <span
                        className={`status-tag ${submission.status === "late" || submission.status === "missing" ? "status-failed" : submission.status === "graded" ? "status-downloaded" : ""}`}
                      >
                        {statusLabel(submission.status)}
                      </span>
                    </TableCell>
                    <TableCell>
                      {submission.displayGrade ?? submission.score ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <section
            className="assignment-detail"
            aria-label="提交详情"
            aria-live="polite"
          >
            {selected && (
              <>
                <div className="assignment-detail-heading">
                  <div>
                    <span>{selected.loginId ?? selected.studentId}</span>
                    <h3>{selected.studentName}</h3>
                  </div>
                  <span className="status-tag">
                    第 {selected.attempt ?? 1} 次提交
                  </span>
                </div>
                <dl className="assignment-meta">
                  <div>
                    <dt>提交时间</dt>
                    <dd>{formatDateTime(selected.submittedAt)}</dd>
                  </div>
                  <div>
                    <dt>状态</dt>
                    <dd>{statusLabel(selected.status)}</dd>
                  </div>
                  <div>
                    <dt>附件</dt>
                    <dd>{selected.attachments.length} 个</dd>
                  </div>
                </dl>
                <Tabs
                  value={detailTab}
                  onValueChange={(value) =>
                    setDetailTab(value as typeof detailTab)
                  }
                >
                  <TabsList aria-label="批改详情">
                    <TabsTrigger value="submission">提交详情</TabsTrigger>
                    <TabsTrigger value="comments">评论</TabsTrigger>
                    <TabsTrigger value="references">参考文件</TabsTrigger>
                  </TabsList>
                </Tabs>

                {detailTab === "submission" && (
                  <div className="grid gap-4">
                    {!readOnly && onSaveScore && (
                      <form
                        className="submission-forms"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void saveScore();
                        }}
                      >
                        <label htmlFor="grading-score">
                          评分{maxScore != null ? `（满分 ${maxScore}）` : ""}
                        </label>
                        <div className="flex items-center gap-2">
                          <input
                            id="grading-score"
                            inputMode="decimal"
                            value={scoreDraft}
                            disabled={Boolean(busy)}
                            onChange={(event) =>
                              setScoreDraft(event.target.value)
                            }
                          />
                          <Button type="submit" loading={busy === "score"}>
                            保存评分
                          </Button>
                        </div>
                      </form>
                    )}
                    {selected.body && (
                      <RichPreviewContent
                        kind="html"
                        title="在线提交内容"
                        content={selected.body}
                      />
                    )}
                    {selected.attachments.length > 0 ? (
                      <div className="list-surface">
                        {selected.attachments.map((attachment) => (
                          <div className="list-row" key={attachment.id}>
                            <div className="min-w-0">
                              <p className="truncate font-medium">
                                {attachment.name}
                              </p>
                              <span>
                                {attachment.downloadStatus === "failed"
                                  ? "下载失败"
                                  : attachment.downloadStatus === "downloaded"
                                    ? "已下载"
                                    : "提交文件"}
                              </span>
                            </div>
                            <div className="finder-actions">
                              {attachment.preview && (
                                <Button
                                  variant="link"
                                  size="sm"
                                  onClick={() =>
                                    document
                                      .getElementById(
                                        `preview-${attachment.id}`,
                                      )
                                      ?.focus()
                                  }
                                >
                                  <Eye aria-hidden="true" />
                                  预览
                                </Button>
                              )}
                              {onDownloadAttachment && (
                                <Button
                                  variant="link"
                                  size="sm"
                                  loading={
                                    busy === `attachment:${attachment.id}`
                                  }
                                  onClick={() =>
                                    void runFileAction(
                                      `attachment:${attachment.id}`,
                                      () =>
                                        onDownloadAttachment(
                                          attachment,
                                          selected,
                                        ),
                                      `已处理“${attachment.name}”。`,
                                    )
                                  }
                                >
                                  <Download aria-hidden="true" />
                                  下载
                                </Button>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="notice">该提交没有附件。</p>
                    )}
                    {previewAttachment && (
                      <div
                        id={`preview-${selected.attachments.find((item) => item.preview)?.id}`}
                        tabIndex={-1}
                      >
                        <RichPreviewContent {...previewAttachment} />
                      </div>
                    )}
                  </div>
                )}

                {detailTab === "comments" && (
                  <div className="grid gap-4">
                    {selected.comments.length === 0 ? (
                      <EmptyState
                        title="暂无评论"
                        description="可以为该学生添加批改反馈。"
                      />
                    ) : (
                      <div className="list-surface">
                        {selected.comments.map((comment) => (
                          <article className="list-row" key={comment.id}>
                            <div>
                              <p className="font-medium">{comment.author}</p>
                              <span>{comment.content}</span>
                            </div>
                            <span className="row-time">
                              {formatDateTime(comment.createdAt)}
                            </span>
                          </article>
                        ))}
                      </div>
                    )}
                    {!readOnly && onAddComment && (
                      <form
                        className="submission-forms"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void addComment();
                        }}
                      >
                        <label htmlFor="grading-comment">添加评论</label>
                        <textarea
                          id="grading-comment"
                          value={commentDraft}
                          disabled={Boolean(busy)}
                          maxLength={5000}
                          onChange={(event) =>
                            setCommentDraft(event.target.value)
                          }
                        />
                        <Button
                          type="submit"
                          loading={busy === "comment"}
                          disabled={!commentDraft.trim()}
                        >
                          发布评论
                        </Button>
                      </form>
                    )}
                  </div>
                )}

                {detailTab === "references" && (
                  <div>
                    {referenceFiles.length === 0 ? (
                      <EmptyState
                        title="未绑定参考文件"
                        description="绑定评分标准或参考答案后会显示在这里。"
                      />
                    ) : (
                      <div className="list-surface">
                        {referenceFiles.map((file) => (
                          <div className="list-row" key={file.id}>
                            <div className="min-w-0">
                              <p className="truncate font-medium">
                                {file.name}
                              </p>
                              <span>{referenceLabel(file.status)}</span>
                            </div>
                            {onOpenReference && (
                              <Button
                                variant="link"
                                size="sm"
                                disabled={file.status === "downloading"}
                                loading={busy === `reference:${file.id}`}
                                onClick={() =>
                                  void runFileAction(
                                    `reference:${file.id}`,
                                    () => onOpenReference(file),
                                    `已打开“${file.name}”。`,
                                  )
                                }
                              >
                                打开
                              </Button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
