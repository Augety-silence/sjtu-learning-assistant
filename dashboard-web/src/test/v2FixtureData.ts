// 2.0 全页面视觉夹具的示例数据，生产入口不引用本文件。
const now = new Date().toISOString();
const document = {
  id: "a".repeat(32),
  source_id: "automatic-courses",
  source_name: "课程资料",
  title: "第一讲.pptx.md",
  relative_path: "机器学习/第一讲.pptx.md",
  folder: "机器学习",
  status: "completed",
  match_offset: 0,
  snippet: "线性模型与监督学习",
};
const files = [
  { name: "课程笔记", is_directory: true, size: null },
  { name: "机器学习学习计划.md", is_directory: false, size: 3480 },
  { name: "第一讲课件.pdf", is_directory: false, size: 4820400 },
  { name: "实验代码.zip", is_directory: false, size: 98720 },
  { name: "课表备份.ics", is_directory: false, size: 2394 },
].map((item, i) => ({
  ...item,
  id: `drive-${i}`,
  path: [item.name],
  modified_at: now,
}));
export function extraV2FixtureData(
  action: string,
  payload: Record<string, unknown>,
): unknown {
  if (action === "markdown_sync_progress")
    return {
      courses: {
        total: 210,
        completed: 189,
        pending: 18,
        failed: 3,
        ai_completed: 152,
        ai_failed: 3,
        issues: [{ name: "第三章课件.pdf", code: "network" }],
        truncated: false,
      },
      transcripts: {
        total: 76,
        completed: 76,
        pending: 0,
        failed: 0,
        ai_completed: 65,
        ai_failed: 0,
        issues: [],
        truncated: false,
      },
      automatic: true,
      ai_ready: true,
      current: {
        status: "running",
        phase: "local_text",
        current_file: "课程讲义.pdf",
      },
      updated_at: new Date().toISOString(),
    };
  if (action === "sync_automation_status")
    return { enabled: true, interval_seconds: 900 };
  if (action === "native_calendar_status")
    return { supported: true, enabled: false, authorization: "not_determined" };
  if (action === "timetable_schedule") {
    const events = [
      { title: "机器学习导论", hour: 10, end: 11.5, location: "电院 3-215" },
      { title: "课程答疑", hour: 13.5, end: 14.5, location: "电院 3-215" },
      { title: "读书小组讨论", hour: 19, end: 20, location: "图书馆" },
    ].map((e, i) => {
      const start = new Date();
      start.setHours(Math.floor(e.hour), (e.hour % 1) * 60, 0, 0);
      const end = new Date(start);
      end.setHours(Math.floor(e.end), (e.end % 1) * 60, 0, 0);
      return {
        id: `course-${i}`,
        title: e.title,
        startAt: start.toISOString(),
        endAt: end.toISOString(),
        courseName: e.title,
        source: "timetable",
        location: e.location,
      };
    });
    return { events };
  }
  if (action === "drive_list") {
    const path = Array.isArray(payload.path) ? payload.path : [];
    const items = path.length
      ? files
          .filter((f) => !f.is_directory)
          .map((f) => ({ ...f, path: [...path, f.name] }))
      : files;
    return {
      path,
      page: 1,
      page_size: 50,
      total: items.length,
      pages: 1,
      folder_count: path.length ? 0 : 1,
      file_count: items.filter((f) => !f.is_directory).length,
      truncated: false,
      query: "",
      sort: { by: "name", order: "asc" },
      items,
    };
  }
  if (action === "drive_space")
    return {
      total: 10 * 1024 ** 3,
      used: 1.4 * 1024 ** 3,
      available: 8.6 * 1024 ** 3,
    };
  if (action === "drive_transfers") return { items: [] };
  if (action === "compiled_knowledge_sources")
    return {
      items: [
        {
          id: "automatic-courses",
          name: "自动课件 Markdown",
          kind: "automatic",
          root: "课程知识库",
          count: 8,
        },
      ],
      count: 8,
      issues: [],
      truncated: false,
    };
  if (action === "compiled_knowledge_search") {
    const documents = Array.from({ length: 28 }, (_, i) => ({
      ...document,
      id: String(i + 1).padStart(32, "0"),
      title: `第${i + 1}讲.pptx.md`,
      folder: i < 24 ? "机器学习" : "机器学习/练习",
      relative_path: `机器学习/${i < 24 ? "" : "练习/"}第${i + 1}讲.pptx.md`,
    }));
    const matching = documents.filter((doc) =>
      payload.direct_children
        ? doc.folder === (payload.folder || "")
        : !payload.folder ||
          doc.folder === payload.folder ||
          doc.folder.startsWith(String(payload.folder) + "/"),
    );
    const offset = Number(payload.offset || 0),
      limit = Number(payload.limit || 20);
    return {
      items: matching.slice(offset, offset + limit),
      count: matching.length,
      next_offset: offset + limit < matching.length ? offset + limit : null,
      issues: [],
      truncated: false,
      folders: [
        {
          source_id: "automatic-courses",
          path: "机器学习",
          name: "机器学习",
          count: 28,
        },
        {
          source_id: "automatic-courses",
          path: "机器学习/练习",
          name: "练习",
          count: 4,
        },
      ],
    };
  }
  if (action === "compiled_knowledge_read")
    return {
      ...document,
      id: payload.document_id,
      content_sha256: "fixture-version-1",
      text: "# 监督学习与线性模型\n\n本页为独立验收示例。\n\n## 本讲知识点\n\n通过训练样本学习输入与输出之间的映射，使用损失函数评价预测结果。\n\n| 概念 | 阅读线索 |\n| --- | --- |\n| 线性回归 | 连续值预测 |\n| 分类 | 离散类别预测 |\n\n## 学习建议\n\n1. 明确输入特征与目标变量。\n2. 对照原始课件完成公式推导。",
      citation: "机器学习/第一讲.pptx.md",
      section: "第1页",
      offset: 0,
      next_offset: null,
      total_chars: 300,
      truncated: false,
    };
  if (action === "knowledge_mcp_config")
    return { command: "SJTU Learning Assistant", args: ["--mcp-stdio"] };
  if (action === "course_markdown_status")
    return {
      enabled: true,
      status: "running",
      output_root: "课程知识库",
      daily_limit: 300,
      calls_today: 12,
      total: 38,
      completed: 32,
      pending: 4,
      failed: 2,
      issues: ["2 个产物等待手动重试"],
    };
  if (action === "provider_usage_summary")
    return {
      periods: Object.fromEntries(
        ["today", "week", "all"].map((key) => [
          key,
          {
            calls: 0,
            metrics: Object.fromEntries(
              ["input", "output", "cache", "total"].map((metric) => [
                metric,
                { tokens: 0, reported_calls: 0, missing_calls: 0 },
              ]),
            ),
          },
        ]),
      ),
      first_recorded_at: null,
      timezone: "Asia/Shanghai",
      scope: "local",
    };
  if (action === "ai_workspace_status")
    return {
      configured: false,
      available: false,
      name: null,
      path: null,
      reason: null,
    };
  if (action === "course_catalog")
    return {
      count: 2,
      complete: true,
      refreshed_at: now,
      terms: [
        {
          id: "term-1",
          name: "2026 秋",
          courses: [
            {
              id: "101",
              name: "文本分析与大模型",
              current: true,
              access: "available",
            },
          ],
        },
      ],
    };
  return undefined;
}
