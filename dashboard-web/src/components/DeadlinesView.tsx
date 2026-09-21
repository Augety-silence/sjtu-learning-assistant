import { useCallback, useEffect, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { getJson } from "@/lib/api";
import { deadlineDistance, formatDateTime } from "@/lib/format";
import type { Deadline } from "@/lib/types";

const windows = [
  { value: "24h", label: "24 小时" },
  { value: "7d", label: "7 天" },
  { value: "14d", label: "14 天" },
];

export function DeadlinesView() {
  const [windowValue, setWindowValue] = useState("7d");
  const [items, setItems] = useState<Deadline[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setItems(null);
    setError("");
    try {
      const data = await getJson<{ items: Deadline[] }>(
        `/api/deadlines?window=${windowValue}`,
      );
      setItems(data.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "未知错误");
    }
  }, [windowValue]);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="section-stack">
      <div className="view-intro">
        <div>
          <h2>待处理作业</h2>
          <p>仅显示所选时间范围内尚未完成的作业。</p>
        </div>
        <Tabs value={windowValue} onValueChange={setWindowValue}>
          <TabsList>
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
          title="没有临近截止事项"
          description="当前时间范围内没有待完成作业。"
        />
      ) : (
        <div className="table-surface">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="deadline-title">事项</TableHead>
                <TableHead className="deadline-course">课程</TableHead>
                <TableHead className="deadline-time">截止时间</TableHead>
                <TableHead className="deadline-state">状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow key={item.source_id}>
                  <TableCell>
                    <a
                      className="table-link"
                      href={item.url || undefined}
                      target={item.url ? "_blank" : undefined}
                      rel="noreferrer"
                    >
                      {item.title}
                    </a>
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
          </Table>
        </div>
      )}
    </div>
  );
}
