import { Download, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
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

export interface RosterMember {
  id: string;
  name: string;
  sortableName?: string | null;
  loginId?: string | null;
  email?: string | null;
  role: string;
  section?: string | null;
  joinedAt?: string | null;
}

export interface RosterExportRequest {
  members: RosterMember[];
  scope: "filtered" | "selected";
  format: "csv";
}

export interface RosterViewProps {
  courseName?: string;
  members: RosterMember[];
  loading?: boolean;
  error?: string | null;
  permissionDenied?: boolean;
  exporting?: boolean;
  onRetry?: () => void | Promise<void>;
  onExport?: (request: RosterExportRequest) => void | Promise<void>;
}

function formatDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(date);
}

export function RosterView({
  courseName,
  members,
  loading = false,
  error,
  permissionDenied = false,
  exporting = false,
  onRetry,
  onExport,
}: RosterViewProps) {
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("all");
  const [section, setSection] = useState("all");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [localExporting, setLocalExporting] = useState(false);
  const { showToast } = useToast();
  const roles = useMemo(
    () => Array.from(new Set(members.map((member) => member.role))).sort(),
    [members],
  );
  const sections = useMemo(
    () =>
      Array.from(
        new Set(
          members
            .map((member) => member.section)
            .filter((value): value is string => Boolean(value)),
        ),
      ).sort(),
    [members],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return members.filter((member) => {
      const matchesQuery =
        !needle ||
        [member.name, member.sortableName, member.loginId, member.email].some(
          (value) => value?.toLocaleLowerCase().includes(needle),
        );
      return (
        matchesQuery &&
        (role === "all" || member.role === role) &&
        (section === "all" || member.section === section)
      );
    });
  }, [members, query, role, section]);
  const selected = filtered.filter((member) => selectedIds.has(member.id));
  const allVisibleSelected =
    filtered.length > 0 && selected.length === filtered.length;

  const runExport = async (scope: "filtered" | "selected") => {
    if (!onExport || localExporting || exporting) return;
    const targets = scope === "selected" ? selected : filtered;
    if (targets.length === 0) {
      showToast({
        kind: "info",
        message:
          scope === "selected"
            ? "请先选择要导出的成员。"
            : "当前筛选没有可导出的成员。",
      });
      return;
    }
    setLocalExporting(true);
    try {
      await onExport({ members: targets, scope, format: "csv" });
      showToast({
        kind: "success",
        message: `已导出 ${targets.length} 名课程成员。`,
      });
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "名单导出失败",
      });
    } finally {
      setLocalExporting(false);
    }
  };

  if (permissionDenied)
    return (
      <EmptyState
        title="没有成员访问权限"
        description="当前 Canvas 账号无法读取此课程的成员名单。"
      />
    );
  if (error) return <ErrorState message={error} retry={onRetry} />;
  if (loading) return <LoadingState label="正在加载课程成员…" />;

  return (
    <div className="section-stack">
      <div className="view-intro">
        <div>
          <h2>课程花名册</h2>
          <p>
            {courseName ? `${courseName} · ` : ""}筛选、选择并导出课程成员。
          </p>
        </div>
        <div className="message-toolbar">
          <span className="text-sm text-secondary">CSV（UTF-8）</span>
          <Button
            variant="outline"
            size="sm"
            disabled={
              !onExport || localExporting || exporting || selected.length === 0
            }
            loading={localExporting || exporting}
            onClick={() => void runExport("selected")}
          >
            <Download aria-hidden="true" />
            导出所选
          </Button>
          <Button
            size="sm"
            disabled={
              !onExport || localExporting || exporting || filtered.length === 0
            }
            onClick={() => void runExport("filtered")}
          >
            <Download aria-hidden="true" />
            导出筛选结果
          </Button>
        </div>
      </div>

      <div className="finder-toolbar" role="search">
        <label className="search-control">
          <Search aria-hidden="true" />
          <input
            type="search"
            aria-label="搜索课程成员"
            placeholder="搜索姓名、学号或邮箱"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label>
          <span>角色</span>
          <select
            aria-label="按角色筛选"
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            <option value="all">全部角色</option>
            {roles.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>班级</span>
          <select
            aria-label="按班级筛选"
            value={section}
            onChange={(event) => setSection(event.target.value)}
          >
            <option value="all">全部班级</option>
            {sections.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        {(query || role !== "all" || section !== "all") && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setQuery("");
              setRole("all");
              setSection("all");
            }}
          >
            清除筛选
          </Button>
        )}
      </div>
      <p className="result-count" aria-live="polite">
        显示 {filtered.length} / {members.length} 人，已选择 {selected.length}{" "}
        人
      </p>

      {members.length === 0 ? (
        <EmptyState
          title="暂无课程成员"
          description="选择课程或完成 Canvas 同步后再查看。"
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="没有匹配成员"
          description="请调整搜索词、角色或班级筛选。"
          action={
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setQuery("");
                setRole("all");
                setSection("all");
              }}
            >
              清除筛选
            </Button>
          }
        />
      ) : (
        <div className="table-surface">
          <Table aria-label="课程成员名单" className="min-w-[820px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">
                  <input
                    type="checkbox"
                    aria-label="选择全部筛选结果"
                    checked={allVisibleSelected}
                    onChange={(event) =>
                      setSelectedIds((current) => {
                        const next = new Set(current);
                        for (const member of filtered)
                          event.target.checked
                            ? next.add(member.id)
                            : next.delete(member.id);
                        return next;
                      })
                    }
                  />
                </TableHead>
                <TableHead>姓名</TableHead>
                <TableHead>学号 / 登录名</TableHead>
                <TableHead>邮箱</TableHead>
                <TableHead>角色</TableHead>
                <TableHead>班级</TableHead>
                <TableHead>加入时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((member) => (
                <TableRow key={member.id}>
                  <TableCell>
                    <input
                      type="checkbox"
                      aria-label={`选择 ${member.name}`}
                      checked={selectedIds.has(member.id)}
                      onChange={(event) =>
                        setSelectedIds((current) => {
                          const next = new Set(current);
                          event.target.checked
                            ? next.add(member.id)
                            : next.delete(member.id);
                          return next;
                        })
                      }
                    />
                  </TableCell>
                  <TableCell className="font-medium">{member.name}</TableCell>
                  <TableCell>{member.loginId ?? "—"}</TableCell>
                  <TableCell>{member.email ?? "—"}</TableCell>
                  <TableCell>
                    <span className="status-tag">{member.role}</span>
                  </TableCell>
                  <TableCell>{member.section ?? "—"}</TableCell>
                  <TableCell>{formatDate(member.joinedAt)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
