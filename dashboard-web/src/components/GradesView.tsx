import { useMemo, useState } from "react";
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

export interface GradeAssignment {
  id: string;
  name: string;
  pointsPossible: number | null;
}

export interface StudentGradeCell {
  score: number | null;
  displayGrade?: string | null;
  status?: "unsubmitted" | "submitted" | "late" | "graded";
  editable?: boolean;
}

export interface StudentGradeRow {
  studentId: string;
  studentName: string;
  loginId?: string | null;
  grades: Record<string, StudentGradeCell | undefined>;
}

export interface GradeSaveInput {
  studentId: string;
  assignmentId: string;
  score: number | null;
}

export interface GradesViewProps {
  courseName?: string;
  assignments: GradeAssignment[];
  students: StudentGradeRow[];
  loading?: boolean;
  error?: string | null;
  permissionDenied?: boolean;
  readOnly?: boolean;
  onRetry?: () => void | Promise<void>;
  onSaveGrade?: (input: GradeSaveInput) => void | Promise<void>;
}

function gradeKey(studentId: string, assignmentId: string) {
  return `${studentId}:${assignmentId}`;
}

function numericScores(students: StudentGradeRow[], assignmentId?: string) {
  return students.flatMap((student) => {
    const cells = assignmentId
      ? [student.grades[assignmentId]]
      : Object.values(student.grades);
    return cells.flatMap((cell) =>
      typeof cell?.score === "number" ? [cell.score] : [],
    );
  });
}

function average(values: number[]) {
  return values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : 0;
}

export function GradesView({
  courseName,
  assignments,
  students,
  loading = false,
  error,
  permissionDenied = false,
  readOnly = false,
  onRetry,
  onSaveGrade,
}: GradesViewProps) {
  const [view, setView] = useState<"grades" | "statistics">("grades");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const { showToast } = useToast();
  const scores = useMemo(() => numericScores(students), [students]);
  const totalCells = students.length * assignments.length;
  const submittedCount = students.reduce(
    (count, student) =>
      count +
      assignments.filter((assignment) => {
        const status = student.grades[assignment.id]?.status;
        return status && status !== "unsubmitted";
      }).length,
    0,
  );

  const save = async (
    student: StudentGradeRow,
    assignment: GradeAssignment,
  ) => {
    if (!onSaveGrade) return;
    const key = gradeKey(student.studentId, assignment.id);
    const raw =
      drafts[key] ?? String(student.grades[assignment.id]?.score ?? "");
    const score = raw.trim() === "" ? null : Number(raw);
    if (
      score !== null &&
      (!Number.isFinite(score) ||
        score < 0 ||
        (assignment.pointsPossible !== null &&
          score > assignment.pointsPossible))
    ) {
      showToast({
        kind: "error",
        message: `请输入 0 至 ${assignment.pointsPossible ?? "有效上限"} 的分数。`,
      });
      return;
    }
    setSavingKey(key);
    try {
      await onSaveGrade({
        studentId: student.studentId,
        assignmentId: assignment.id,
        score,
      });
      showToast({
        kind: "success",
        message: `已保存 ${student.studentName} 的成绩。`,
      });
      setDrafts((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "成绩保存失败",
      });
    } finally {
      setSavingKey(null);
    }
  };

  if (permissionDenied) {
    return (
      <EmptyState
        title="没有成绩访问权限"
        description="仅课程教师、助教或获得授权的成员可以查看评分册。"
      />
    );
  }
  if (error) return <ErrorState message={error} retry={onRetry} />;
  if (loading) return <LoadingState label="正在加载学生成绩…" />;

  return (
    <div className="section-stack">
      <div className="view-intro">
        <div>
          <h2>评分册</h2>
          <p>
            {courseName ? `${courseName} · ` : ""}查看成绩统计与学生成绩明细。
          </p>
        </div>
        <Tabs
          value={view}
          onValueChange={(value) => setView(value as "grades" | "statistics")}
        >
          <TabsList aria-label="成绩视图">
            <TabsTrigger value="grades">学生成绩</TabsTrigger>
            <TabsTrigger value="statistics">统计</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <dl className="summary-strip" aria-label="成绩概览">
        <div className="summary-item summary-secondary">
          <dt>学生人数</dt>
          <dd>{students.length}</dd>
          <span>当前课程</span>
        </div>
        <div className="summary-item summary-priority">
          <dt>已评分</dt>
          <dd>{scores.length}</dd>
          <span>共 {totalCells} 个成绩单元</span>
        </div>
        <div className="summary-item summary-urgent">
          <dt>平均分</dt>
          <dd>{scores.length ? average(scores).toFixed(1) : "—"}</dd>
          <span>已录入成绩</span>
        </div>
      </dl>

      {students.length === 0 || assignments.length === 0 ? (
        <EmptyState
          title="暂无成绩数据"
          description="选择含有学生与作业的课程后再查看。"
          action={
            onRetry ? (
              <Button variant="outline" onClick={() => void onRetry()}>
                重新检查
              </Button>
            ) : undefined
          }
        />
      ) : view === "statistics" ? (
        <Section title="作业统计">
          <div className="table-surface">
            <Table aria-label="作业成绩统计">
              <TableHeader>
                <TableRow>
                  <TableHead>作业</TableHead>
                  <TableHead>已评分</TableHead>
                  <TableHead>平均分</TableHead>
                  <TableHead>最高 / 最低</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {assignments.map((assignment) => {
                  const values = numericScores(students, assignment.id);
                  return (
                    <TableRow key={assignment.id}>
                      <TableCell className="font-medium">
                        {assignment.name}
                      </TableCell>
                      <TableCell>
                        {values.length} / {students.length}
                      </TableCell>
                      <TableCell>
                        {values.length ? average(values).toFixed(1) : "—"}
                      </TableCell>
                      <TableCell>
                        {values.length
                          ? `${Math.max(...values)} / ${Math.min(...values)}`
                          : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </Section>
      ) : (
        <Section
          title="学生成绩"
          action={
            <span className="result-count">
              已提交 {submittedCount} / {totalCells}
            </span>
          }
        >
          <div className="table-surface">
            <Table aria-label="学生成绩表" className="min-w-[720px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-44">学生</TableHead>
                  {assignments.map((assignment) => (
                    <TableHead key={assignment.id}>
                      <span title={assignment.name}>{assignment.name}</span>
                      <br />
                      <span className="font-normal">
                        满分 {assignment.pointsPossible ?? "—"}
                      </span>
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {students.map((student) => (
                  <TableRow key={student.studentId}>
                    <TableCell>
                      <strong className="font-medium">
                        {student.studentName}
                      </strong>
                      <br />
                      <span className="text-xs text-caption">
                        {student.loginId ?? student.studentId}
                      </span>
                    </TableCell>
                    {assignments.map((assignment) => {
                      const cell = student.grades[assignment.id];
                      const key = gradeKey(student.studentId, assignment.id);
                      const editable = Boolean(
                        onSaveGrade && !readOnly && cell?.editable !== false,
                      );
                      return (
                        <TableCell
                          key={assignment.id}
                          className="overflow-visible"
                        >
                          <div className="grid gap-1">
                            {editable ? (
                              <input
                                className="h-9 w-full rounded-control border border-component bg-surface-elevated px-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                                aria-label={`${student.studentName}，${assignment.name}，分数`}
                                inputMode="decimal"
                                value={drafts[key] ?? String(cell?.score ?? "")}
                                disabled={savingKey !== null}
                                onChange={(event) =>
                                  setDrafts((current) => ({
                                    ...current,
                                    [key]: event.target.value,
                                  }))
                                }
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") {
                                    event.preventDefault();
                                    void save(student, assignment);
                                  }
                                }}
                                onBlur={() => {
                                  if (key in drafts)
                                    void save(student, assignment);
                                }}
                              />
                            ) : (
                              <span>
                                {cell?.displayGrade ?? cell?.score ?? "—"}
                              </span>
                            )}
                            <span
                              className={`status-tag ${cell?.status === "late" || cell?.status === "unsubmitted" ? "status-failed" : cell?.status === "graded" ? "status-downloaded" : ""}`}
                            >
                              {cell?.status === "late"
                                ? "迟交"
                                : cell?.status === "unsubmitted"
                                  ? "未提交"
                                  : cell?.status === "graded"
                                    ? "已评分"
                                    : cell?.status === "submitted"
                                      ? "已提交"
                                      : "—"}
                            </span>
                          </div>
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Section>
      )}
    </div>
  );
}
