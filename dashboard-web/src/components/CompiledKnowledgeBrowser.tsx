import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/Button";
import {
  getKnowledgeMCPConfig,
  listCompiledKnowledgeSources,
  openCompiledKnowledge,
  readCompiledKnowledge,
  searchCompiledKnowledge,
} from "@/lib/api";
import type {
  CompiledKnowledgeDocument,
  CompiledKnowledgePage,
  CompiledKnowledgeSearch,
  CompiledKnowledgeSource,
} from "@/lib/types";
import "./compiled-knowledge.css";

const statusLabels = {
  completed: "多模态完成",
  pending: "全文就绪 · 视觉待编译",
  failed: "全文就绪 · 视觉异常",
  compiled: "项目编译结果",
};

export function CompiledKnowledgeBrowser({
  preferredSource = "",
}: {
  preferredSource?: string;
}) {
  const [sources, setSources] = useState<CompiledKnowledgeSource[]>([]);
  const [sourceId, setSourceId] = useState(preferredSource);
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [search, setSearch] = useState<CompiledKnowledgeSearch | null>(null);
  const [page, setPage] = useState<CompiledKnowledgePage | null>(null);
  const [loading, setLoading] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [config, setConfig] = useState("");
  const searchGeneration = useRef(0);
  const readGeneration = useRef(0);
  const sourceGeneration = useRef(0);

  const read = useCallback(
    async (doc: CompiledKnowledgeDocument, offset = 0) => {
      const generation = ++readGeneration.current;
      setReading(true);
      setPage(null);
      setError("");
      try {
        const result = await readCompiledKnowledge(doc.id, offset);
        if (generation === readGeneration.current) setPage(result);
      } catch (reason) {
        if (generation === readGeneration.current)
          setError(reason instanceof Error ? reason.message : "结果读取失败");
      } finally {
        if (generation === readGeneration.current) setReading(false);
      }
    },
    [],
  );

  const load = useCallback(
    async (selected: string, keywords = "", offset = 0) => {
      const generation = ++searchGeneration.current;
      ++readGeneration.current;
      setReading(false);
      setPage(null);
      setLoading(true);
      setActiveQuery(keywords);
      setError("");
      try {
        const result = await searchCompiledKnowledge(
          keywords,
          selected,
          offset,
        );
        if (generation !== searchGeneration.current) return;
        setSearch(result);
        if (result.items[0])
          void read(
            result.items[0],
            Math.max(0, (result.items[0].match_offset ?? 0) - 200),
          );
      } catch (reason) {
        if (generation === searchGeneration.current)
          setError(
            reason instanceof Error ? reason.message : "编译结果加载失败",
          );
      } finally {
        if (generation === searchGeneration.current) setLoading(false);
      }
    },
    [read],
  );

  const refresh = useCallback(async () => {
    const generation = ++sourceGeneration.current;
    try {
      const result = await listCompiledKnowledgeSources();
      if (generation !== sourceGeneration.current) return;
      setSources(result.items);
      if (result.issues.length) setMessage(result.issues.join("；"));
      else setMessage("");
      const selected = result.items.some((item) => item.id === preferredSource)
        ? preferredSource
        : "";
      setSourceId(selected);
      setQuery("");
      await load(selected);
    } catch (reason) {
      if (generation === sourceGeneration.current)
        setError(reason instanceof Error ? reason.message : "知识来源加载失败");
    }
  }, [preferredSource, load]);

  useEffect(() => {
    void refresh();
    return () => {
      ++searchGeneration.current;
      ++readGeneration.current;
      ++sourceGeneration.current;
    };
  }, [refresh]);

  const open = async (doc?: CompiledKnowledgeDocument) => {
    try {
      const selected = doc?.source_id || sourceId || sources[0]?.id;
      if (!selected) return;
      await openCompiledKnowledge(selected, doc?.id);
      setMessage("已在系统文件管理器打开结果位置。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "结果目录打开失败");
    }
  };

  const connection = async () => {
    try {
      const result = await getKnowledgeMCPConfig();
      setConfig(
        JSON.stringify(
          {
            mcpServers: {
              "sjtu-knowledge": {
                command: result.command,
                args: result.args,
                ...(result.env ? { env: result.env } : {}),
              },
            },
          },
          null,
          2,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "连接配置读取失败");
    }
  };

  return (
    <section
      className="knowledge-config compiled-knowledge"
      aria-label="编译结果与知识点"
    >
      <div className="section-header">
        <div>
          <h2>编译结果与知识点</h2>
          <p>
            直接阅读已有 Markdown，或按知识点搜索正文；内置 AI
            已接入同一只读知识库。
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => void refresh()}
          disabled={loading}
        >
          刷新结果
        </Button>
      </div>
      <form
        className="compiled-knowledge-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          void load(sourceId, query);
        }}
      >
        <label>
          知识来源
          <select
            value={sourceId}
            onChange={(event) => {
              setSourceId(event.target.value);
              void load(event.target.value, query);
            }}
          >
            <option value="">全部编译结果</option>
            {sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name}（{source.count}）
              </option>
            ))}
          </select>
        </label>
        <label>
          搜索知识点
          <input
            type="search"
            maxLength={160}
            value={query}
            placeholder="课程名、知识点或公式关键词"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <Button type="submit" disabled={loading}>
          搜索正文
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!sources.length}
          onClick={() => void open()}
        >
          打开结果目录
        </Button>
      </form>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {loading && <p role="status">正在读取编译结果…</p>}
      {search && (
        <p role="status">
          找到 {search.count} 份结果
          {search.truncated ? "（范围截断，请缩小来源）" : ""}
        </p>
      )}
      {search?.issues.map((issue) => (
        <p key={issue} role="status">
          {issue}
        </p>
      ))}
      {search && !search.items.length && !loading && (
        <p>
          {query
            ? "未找到匹配知识点，请换关键词或知识来源。"
            : "此来源暂无可读取的编译结果；自动课件生成后可刷新查看。"}
        </p>
      )}
      <div className="compiled-knowledge-layout">
        <div className="compiled-knowledge-list" aria-label="Markdown 结果列表">
          {search?.items.map((doc) => (
            <Button
              key={doc.id}
              variant="ghost"
              aria-pressed={page?.id === doc.id}
              className="compiled-knowledge-item"
              onClick={() =>
                void read(doc, Math.max(0, (doc.match_offset ?? 0) - 200))
              }
            >
              <strong>{doc.title}</strong>
              <span>{statusLabels[doc.status]}</span>
              <small>{doc.relative_path}</small>
              {doc.snippet && <small>{doc.snippet}</small>}
            </Button>
          ))}
          {search?.next_offset != null && (
            <Button
              variant="outline"
              onClick={() =>
                void load(sourceId, activeQuery, search.next_offset ?? 0)
              }
              disabled={loading}
            >
              下一组结果
            </Button>
          )}
          {search && (
            <Button
              variant="ghost"
              onClick={() => void load(sourceId, activeQuery)}
              disabled={loading}
            >
              返回首组结果
            </Button>
          )}
        </div>
        <div
          className="compiled-knowledge-reader"
          aria-label="Markdown 正文预览"
        >
          {reading && <p role="status">正在读取正文…</p>}
          {page && (
            <>
              <h3>{page.title}</h3>
              <p>
                {statusLabels[page.status]} · {page.section}
              </p>
              <p className="compiled-knowledge-citation">
                来源：{page.citation}
              </p>
              <div className="compiled-knowledge-toolbar">
                <Button variant="outline" onClick={() => void open(page)}>
                  定位 Markdown 文件及原图
                </Button>
                <Button
                  variant="ghost"
                  disabled={page.offset === 0}
                  onClick={() => void read(page)}
                >
                  从头阅读
                </Button>
                <Button
                  variant="outline"
                  disabled={page.next_offset == null}
                  onClick={() => void read(page, page.next_offset ?? 0)}
                >
                  继续阅读
                </Button>
              </div>
              <div className="rich-preview-markdown">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  skipHtml
                  components={{
                    img: ({ alt }) => (
                      <span>［{alt || "页图"}：请在结果目录查看］</span>
                    ),
                    a: ({ children }) => <span>{children}</span>,
                  }}
                >
                  {page.text}
                </ReactMarkdown>
              </div>
              <p>
                已读到{" "}
                {Math.min(page.offset + page.text.length, page.total_chars)} /{" "}
                {page.total_chars} 字符
                {page.next_offset == null ? " · 文档结束" : " · 可继续阅读"}
              </p>
            </>
          )}
        </div>
      </div>
      <details className="compiled-knowledge-connection">
        <summary>外部 AI 工具只读接入</summary>
        <p>
          支持 stdio MCP 的本机 AI
          客户端可使用下方配置。连接后先搜索知识点，再分页读取并引用来源；不自动修改客户端配置。
        </p>
        <Button variant="outline" onClick={() => void connection()}>
          显示 AI 连接配置
        </Button>
        {config && (
          <>
            <textarea
              aria-label="AI 工具连接配置"
              value={config}
              readOnly
              rows={10}
            />
            <p>
              可用工具：list_compiled_knowledge、search_compiled_knowledge、read_compiled_knowledge。
            </p>
          </>
        )}
      </details>
    </section>
  );
}
