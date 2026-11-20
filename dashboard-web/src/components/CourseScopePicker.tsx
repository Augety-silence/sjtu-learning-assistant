import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { invoke } from "@/lib/api";
import "./course-history.css";

export type CourseScope = { termId: string; courseId: string };
type Catalog = {
  count: number;
  complete: boolean;
  refreshed_at: string | null;
  terms: {
    id: string;
    name: string;
    courses: { id: string; name: string; current: boolean; access: string }[];
  }[];
};

export function CourseScopePicker({
  scope,
  onChange,
}: {
  scope: CourseScope;
  onChange: (scope: CourseScope) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const load = useCallback(async (refresh = false) => {
    const request = ++generation.current;
    setBusy(true);
    setError("");
    try {
      const result = await invoke<Catalog>("course_catalog", { refresh });
      if (request === generation.current) setCatalog(result);
    } catch (reason) {
      if (request === generation.current)
        setError(reason instanceof Error ? reason.message : "目录加载失败");
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }, []);
  useEffect(() => {
    void load();
    return () => {
      ++generation.current;
    };
  }, [load]);
  const selectedTerm = catalog?.terms.find((term) => term.id === scope.termId);
  return (
    <div className="course-scope-picker">
      <label>
        学期
        <select
          aria-label="课程学期"
          value={scope.termId}
          onChange={(event) =>
            onChange({ termId: event.target.value, courseId: "" })
          }
        >
          <option value="">全部已同步学期</option>
          {catalog?.terms.map((term) => (
            <option key={term.id} value={term.id}>
              {term.name} · {term.courses.length} 门
            </option>
          ))}
        </select>
      </label>
      <label>
        课程
        <select
          aria-label="学期课程"
          value={scope.courseId}
          disabled={!selectedTerm}
          onChange={(event) =>
            onChange({ ...scope, courseId: event.target.value })
          }
        >
          <option value="">本学期全部课程</option>
          {selectedTerm?.courses.map((course) => (
            <option key={course.id} value={course.id}>
              {course.name}
              {course.current ? " · 默认同步" : ""}
              {course.access === "restricted" ? " · 日期受限" : ""}
            </option>
          ))}
        </select>
      </label>
      <Button variant="ghost" disabled={busy} onClick={() => void load(true)}>
        {busy ? "读取目录…" : "发现全部学期"}
      </Button>
      <small>
        {catalog
          ? `${catalog.count} 门缓存课程${catalog.complete ? " · 已发现历史目录" : " · 点击发现全部学期获取历史课程"}`
          : "读取本地目录"}
      </small>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
