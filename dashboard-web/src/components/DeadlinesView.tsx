import { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { invoke, openExternal } from "@/lib/api";
import { deadlineDistance, formatDateTime } from "@/lib/format";
import type { Deadline } from "@/lib/types";
import { useCompactViewport } from "@/lib/useCompactViewport";

const windows = [
  { value: "24h", label: "24 小时" },
  { value: "7d", label: "7 天" },
  { value: "14d", label: "14 天" },
];

type DeadlineGroupId = "today" | "week" | "later" | "undated";

interface DeadlineGroup {
  id: DeadlineGroupId;
  label: string;
  items: Deadline[];
}

const groupLabels: Record<DeadlineGroupId, string> = {
  today: "今天",
  week: "本周",
  later: "更晚",
  undated: "无日期",
};

export function groupDeadlines(items: Deadline[], now = new Date()): DeadlineGroup[] {
  const endOfToday = new Date(now);
  endOfToday.setHours(23, 59, 59, 999);
  const endOfWeek = new Date(endOfToday);
  const daysUntilSunday = (7 - endOfToday.getDay()) % 7;
  endOfWeek.setDate(endOfWeek.getDate() + daysUntilSunday);

  const groups: Record<DeadlineGroupId, Deadline[]> = {
    today: [],
    week: [],
    later: [],
    undated: [],
  };

  for (const item of items) {
    if (!item.due_at) {
      groups.undated.push(item);
      continue;
    }
    const due = new Date(item.due_at);
    if (Number.isNaN(due.getTime())) {
      groups.undated.push(item);
    } else if (due <= endOfToday) {
      groups.today.push(item);
    } else if (due <= endOfWeek) {
      groups.week.push(item);
    } else {
      groups.later.push(item);
    }
  }

  const datedSort = (left: Deadline, right: Deadline) => {
    const byTime =
      new Date(left.due_at as string).getTime() -
      new Date(right.due_at as string).getTime();
    return byTime || left.title.localeCompare(right.title, "zh-CN");
  };
  groups.today.sort(datedSort);
  groups.week.sort(datedSort);
  groups.later.sort(datedSort);

  return (Object.keys(groupLabels) as DeadlineGroupId[])
    .map((id) => ({ id, label: groupLabels[id], items: groups[id] }))
    .filter((group) => group.items.length > 0);
}

export function DeadlinesView() {
  const [windowValue, setWindowValue] = useState("7d");
  const [items, setItems] = useState<Deadline[] | null>(null);
  const [error, setError] = useState("");
  const compact = useCompactViewport();
  const { showToast } = useToast();
  const load = useCallback(async () => {
    setItems(null);
    setError("");
    try {
      const data = await invoke<{ items: Deadline[] }>("deadlines", {
        window: windowValue,
      });
      setItems(data.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "未知错误");
    }
  }, [windowValue]);
  useEffect(() => {
    void load();
  }, [load]);

  const groups = useMemo(() => groupDeadlines(items ?? []), [items]);

  const open = async (url: string | null) => {
    if (!url) return;
    try {
      await openExternal(url);
      showToast({ kind: "success", message: "已在浏览器打开截止事项。" });
    } catch (reason) {
      showToast({
        kind: "error",
        message: reason instanceof Error ? reason.message : "链接打开失败",
      });
    }
  };

  return (
    <div className="section-stack deadlines-page">
      <div className="view-intro">
        <div>
          <h2>待处理作业</h2>
          <p>仅显示所选时间范围内尚未完成的作业。</p>
        </div>
        <Tabs value={windowValue} onValueChange={setWindowValue}>
          <TabsList aria-label="截止时间范围">
            {windows.map((item) => (
              <TabsTrigger key={item.value} value={item.value}>
                {item.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : items === null ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState
          title="暂无截止事项"
          description="系统中有数据后，所选时间范围内的待完成作业会显示在这里。"
        />
      ) : compact ? (
        <div className="deadline-mobile-list" aria-label="截止事项列表">
          {groups.map((group) => (
            <section className="deadline-mobile-group" key={group.id}>
              <h3>
                {group.label}<span>{group.items.length}</span>
              </h3>
              <div className="list-surface">
                {group.items.map((item) => (
                  <button
                    type="button"
                    className="deadline-card"
                    key={item.source_id}
                    disabled={!item.url}
                    onClick={() => void open(item.url)}
                  >
                    <span className="deadline-card-title">{item.title}</span>
                    <span className="deadline-card-course">{item.course}</span>
                    <span className="deadline-card-meta">
                      <span>{formatDateTime(item.due_at)}</span>
                      <span className="status-warning">
                        {deadlineDistance(item.due_at)}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="table-surface deadline-table">
          <Table aria-label="按日期分组的截止事项">
            <TableHeader>
              <TableRow>
                <TableHead className="deadline-title">事项</TableHead>
                <TableHead className="deadline-course">课程</TableHead>
                <TableHead className="deadline-time">截止时间</TableHead>
                <TableHead className="deadline-state">状态</TableHead>
              </TableRow>
            </TableHeader>
            {groups.map((group) => (
              <TableBody key={group.id} aria-label={group.label}>
                <TableRow className="deadline-group-row">
                  <TableCell colSpan={4}>
                    <strong>{group.label}</strong>
                    <span>{group.items.length} 项</span>
                  </TableCell>
                </TableRow>
                {group.items.map((item) => (
                  <TableRow key={item.source_id}>
                    <TableCell>
                      <button
                        type="button"
                        className="table-link"
                        disabled={!item.url}
                        onClick={() => void open(item.url)}
                      >
                        {item.title}
                      </button>
                    </TableCell>
                    <TableCell>{item.course}</TableCell>
                    <TableCell title={formatDateTime(item.due_at)}>
                      {formatDateTime(item.due_at)}
                    </TableCell>
                    <TableCell>
                      <span className="status-warning">
                        {deadlineDistance(item.due_at)}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            ))}
          </Table>
        </div>
      )}
    </div>
  );
}
