import { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { getJson, postJson } from "@/lib/api";
import { formatDateTime, formatSize } from "@/lib/format";
import type { Material, MaterialFilters } from "@/lib/types";

export function MaterialsView() {
  const [filters, setFilters] = useState<MaterialFilters | null>(null);
  const [term, setTerm] = useState("");
  const [course, setCourse] = useState("");
  const [status, setStatus] = useState("all");
  const [items, setItems] = useState<Material[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<Record<string, "downloading" | "opening">>(
    {},
  );
  const [notice, setNotice] = useState("");

  const loadFilters = useCallback(async () => {
    try {
      setFilters(await getJson<MaterialFilters>("/api/materials/filters"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "筛选项加载失败");
    }
  }, []);
  const loadItems = useCallback(async () => {
    setItems(null);
    setError("");
    const params = new URLSearchParams({ status });
    if (term) params.set("term", term);
    if (course) params.set("course_id", course);
    try {
      const result = await getJson<{ items: Material[] }>(
        `/api/materials?${params}`,
      );
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "资料加载失败");
    }
  }, [term, course, status]);
  useEffect(() => {
    void loadFilters();
  }, [loadFilters]);
  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const courses = useMemo(
    () => filters?.courses.filter((item) => !term || item.term === term) ?? [],
    [filters, term],
  );
  const chooseTerm = (value: string) => {
    setTerm(value);
    setCourse("");
  };
  const act = async (item: Material, action: "download" | "open") => {
    setBusy((current) => ({
      ...current,
      [item.source_id]: action === "download" ? "downloading" : "opening",
    }));
    setNotice("");
    try {
      await postJson(`/api/materials/${action}`, { source_id: item.source_id });
      setNotice(
        action === "download"
          ? `“${item.name}”下载完成`
          : `已打开“${item.name}”`,
      );
      if (action === "download") await loadItems();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy((current) => {
        const next = { ...current };
        delete next[item.source_id];
        return next;
      });
    }
  };

  return (
    <div className="section-stack">
      <div className="view-intro">
        <div>
          <h2>课程文件</h2>
          <p>可查看全部学期，按单个文件下载或打开已归档资料。</p>
        </div>
      </div>
      <div className="filter-bar" role="group" aria-label="资料筛选">
        <label>
          <span>学期</span>
          <select
            value={term}
            onChange={(event) => chooseTerm(event.target.value)}
          >
            <option value="">全部学期</option>
            {filters?.terms.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>课程</span>
          <select
            value={course}
            onChange={(event) => setCourse(event.target.value)}
          >
            <option value="">全部课程</option>
            {courses.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>下载状态</span>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="all">全部状态</option>
            <option value="downloaded">已下载</option>
            <option value="pending">未下载</option>
            <option value="failed">下载失败</option>
          </select>
        </label>
      </div>
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      {error ? (
        <ErrorState
          message={error}
          retry={() => {
            void loadFilters();
            void loadItems();
          }}
        />
      ) : items === null ? (
        <LoadingState label="正在读取课程资料…" />
      ) : items.length === 0 ? (
        <EmptyState
          title="没有课程资料"
          description="当前筛选条件下没有可显示的文件。"
        />
      ) : (
        <div className="table-surface">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="col-file">文件</TableHead>
                <TableHead className="col-course">课程</TableHead>
                <TableHead className="col-term">学期</TableHead>
                <TableHead className="col-size">大小</TableHead>
                <TableHead className="col-state">状态</TableHead>
                <TableHead className="col-action">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const current = busy[item.source_id];
                return (
                  <TableRow key={item.source_id}>
                    <TableCell title={item.name}>{item.name}</TableCell>
                    <TableCell title={item.course}>{item.course}</TableCell>
                    <TableCell>{item.term}</TableCell>
                    <TableCell>{formatSize(item.size)}</TableCell>
                    <TableCell title={formatDateTime(item.updated_at)}>
                      <span
                        className={`status-tag status-${item.download_status}`}
                      >
                        {item.download_status === "downloaded"
                          ? "已下载"
                          : item.download_status === "failed"
                            ? "失败"
                            : "未下载"}
                      </span>
                    </TableCell>
                    <TableCell>
                      {item.can_open ? (
                        <Button
                          variant="link"
                          disabled={Boolean(current)}
                          onClick={() => void act(item, "open")}
                        >
                          {current === "opening" ? "打开中…" : "打开"}
                        </Button>
                      ) : (
                        <Button
                          variant="link"
                          disabled={Boolean(current)}
                          onClick={() => void act(item, "download")}
                        >
                          {current === "downloading" ? "下载中…" : "下载"}
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
