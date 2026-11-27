import type { AIToolRun } from "@/lib/types";

/** Only display the saved, redacted summary. No requests or inferred results. */
export function ToolResultPreview({ run }: { run: AIToolRun }) {
  const result = run.result_summary;
  if (!result || typeof result !== "object") return null;
  const data = result as Record<string, unknown>;
  const preview =
    data.preview && typeof data.preview === "object"
      ? (data.preview as Record<string, unknown>)
      : null;
  const items =
    preview?.version === 1 && Array.isArray(preview.items)
      ? preview.items
          .slice(0, 6)
          .filter((value): value is Record<string, unknown> =>
            Boolean(
              value &&
                typeof value === "object" &&
                typeof value.title === "string",
            ),
          )
      : [];
  const text =
    preview?.version === 1 && typeof preview.text === "string"
      ? preview.text.slice(0, 1600)
      : "";
  const facts: [string, string][] = [];
  const number = (label: string, key: string, suffix = "") => {
    const value = data[key];
    if (typeof value === "number" && Number.isFinite(value) && value >= 0)
      facts.push([label, `${value}${suffix}`]);
  };
  if (run.status === "rejected") {
    return (
      <p className="ai-tool-preview-message">
        本次调用未执行，原因见上方记录。
      </p>
    );
  }
  if (typeof data.change_id === "string") {
    facts.push(["文件修改", "已生成提案，等待你确认"]);
    number("新增", "added", " 行");
    number("移除", "removed", " 行");
  } else if (typeof data.returned_chars === "number") {
    number("本次读取", "returned_chars", " 字符");
    number("起始位置", "offset");
    number("全文长度", "total_chars", " 字符");
    if (typeof data.next_offset === "number")
      facts.push(["后续内容", "仍有正文可继续读取"]);
  } else if (typeof data.start_page === "number") {
    number("起始页", "start_page");
    number("结束页", "end_page");
    number("总页数", "unit_count");
    if (typeof data.next_page === "number")
      facts.push(["后续内容", "仍有页面可继续读取"]);
  } else {
    number("返回条目", "count", " 项");
    if (data.count === undefined) number("返回条目", "items_count", " 项");
  }
  if (data.truncated === true)
    facts.push(["读取范围", "结果已截断，可缩小范围继续查询"]);
  return (
    <div className="ai-tool-result" aria-label="返回内容">
      {items.length > 0 && (
        <ul className="ai-tool-result-items">
          {items.map((item, index) => (
            <li key={index}>
              <strong>{String(item.title).slice(0, 180)}</strong>
              {typeof item.detail === "string" && item.detail && (
                <small>{item.detail.slice(0, 480)}</small>
              )}
              {!text && typeof item.excerpt === "string" && item.excerpt && (
                <p>{item.excerpt.slice(0, 480)}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      {text && <p className="ai-tool-result-text">{text}</p>}
      {(preview?.more_items === true || preview?.text_truncated === true) && (
        <p className="ai-tool-preview-message">这里展示部分条目或正文摘录。</p>
      )}
      {!preview && !data.change_id && (
        <p className="ai-tool-preview-message">
          旧记录未保存具体条目和正文；后续调用会在这里展示明细。
        </p>
      )}
      {preview?.version === 1 && !items.length && !text && (
        <p className="ai-tool-preview-message">
          本次调用没有返回可展示的条目或正文。
        </p>
      )}
      {facts.length > 0 && (
        <dl className="ai-tool-preview" aria-label="具体结果">
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
