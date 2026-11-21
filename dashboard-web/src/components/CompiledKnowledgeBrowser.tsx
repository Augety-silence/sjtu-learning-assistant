import {
  FileText,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Search,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { KnowledgeDocumentInfo } from "@/components/KnowledgeDocumentInfo";
import { KnowledgeFolders } from "@/components/KnowledgeFolders";
import { Button } from "@/components/ui/Button";
import {
  getKnowledgeMCPConfig,
  listCompiledKnowledgeSources,
  openCompiledKnowledge,
  readCompiledKnowledge,
  searchCompiledKnowledge,
} from "@/lib/api";
import {
  completeKnowledgeText,
  knowledgeTextLength,
} from "@/lib/compiledKnowledgeCopy";
import {
  savedKnowledgePosition,
  useKnowledgeReadingPosition,
} from "@/lib/knowledgeReadingPosition";
import type {
  CompiledKnowledgeDocument,
  CompiledKnowledgePage,
  CompiledKnowledgeSearch,
  CompiledKnowledgeSource,
} from "@/lib/types";
import "./compiled-knowledge.css";

const statusLabels = {
  completed: "多模态完成",
  pending: "原文已提取 · 知识稿待续编",
  failed: "知识编译未完成",
  compiled: "项目编译结果",
};

function documentTitle(doc: CompiledKnowledgeDocument) {
  return doc.title.replace(/\.(pptx?|pdf)\.md$/i, "").replace(/\.md$/i, "");
}

function documentStatus(doc: CompiledKnowledgeDocument) {
  if (doc.status === "completed" && doc.contract === "course-summary-v1")
    return "精炼课程总结";
  if (doc.status === "completed" && doc.contract === "course-study-v3")
    return "完整课程讲义";
  if (doc.status === "completed" && doc.contract === "course-lecture-v2")
    return "旧版知识稿";
  return statusLabels[doc.status];
}

export function CompiledKnowledgeBrowser({
  preferredSource = "",
}: {
  preferredSource?: string;
}) {
  const [sources, setSources] = useState<CompiledKnowledgeSource[]>([]);
  const [sourceId, setSourceId] = useState(preferredSource);
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [searchOffset, setSearchOffset] = useState(0);
  const [search, setSearch] = useState<CompiledKnowledgeSearch | null>(null);
  const [folders, setFolders] = useState<
    NonNullable<CompiledKnowledgeSearch["folders"]>
  >([]);
  const [page, setPage] = useState<CompiledKnowledgePage | null>(null);
  const [loading, setLoading] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [config, setConfig] = useState("");
  const [folder, setFolder] = useState("");
  const [navigationOpen, setNavigationOpen] = useState(true);
  const readingPosition = useKnowledgeReadingPosition(page);
  const searchGeneration = useRef(0);
  const readGeneration = useRef(0);
  const sourceGeneration = useRef(0);
  const displayedPage = useRef<CompiledKnowledgePage | null>(null);

  const read = useCallback(
    async (
      doc: CompiledKnowledgeDocument,
      offset = 0,
      append = false,
      restart = false,
    ) => {
      readingPosition.capture();
      const generation = ++readGeneration.current;
      setReading(true);
      setError("");
      try {
        let result = await readCompiledKnowledge(doc.id, offset);
        if (
          !append &&
          !restart &&
          result.content_sha256 &&
          result.total_chars > 240_000
        ) {
          const saved = savedKnowledgePosition(doc.id, result.content_sha256);
          if (saved && saved.offset > 0)
            result = await readCompiledKnowledge(doc.id, saved.offset);
        }
        if (
          result.offset === 0 &&
          result.content_sha256 &&
          result.total_chars <= 240_000 &&
          result.next_offset !== null
        ) {
          const text = await completeKnowledgeText(
            doc.id,
            () => generation === readGeneration.current,
            result,
          );
          result = { ...result, text, next_offset: null, truncated: false };
        }
        if (generation === readGeneration.current) {
          const previous = displayedPage.current;
          if (
            append &&
            (!previous ||
              previous.id !== result.id ||
              previous.offset + knowledgeTextLength(previous.text) !==
                result.offset ||
              previous.total_chars !== result.total_chars ||
              previous.content_sha256 !== result.content_sha256)
          )
            throw new Error(
              "讲义版本或分页已变化，请重新打开文件；旧正文已保留。",
            );
          const next =
            append && previous
              ? {
                  ...result,
                  offset: previous.offset,
                  text: previous.text + result.text,
                }
              : result;
          displayedPage.current = next;
          setPage(next);
          if (restart) readingPosition.restart();
        }
      } catch (reason) {
        if (generation === readGeneration.current)
          setError(reason instanceof Error ? reason.message : "结果读取失败");
      } finally {
        if (generation === readGeneration.current) setReading(false);
      }
    },
    [readingPosition.restart, readingPosition.capture],
  );

  const load = useCallback(
    async (
      selected: string,
      keywords = "",
      offset = 0,
      selectedFolder = "",
    ) => {
      readingPosition.capture();
      const generation = ++searchGeneration.current;
      ++readGeneration.current;
      setReading(false);
      setLoading(true);
      setActiveQuery(keywords);
      setSearchOffset(offset);
      setFolder(selectedFolder);
      setError("");
      try {
        const result = await (selectedFolder
          ? searchCompiledKnowledge(keywords, selected, offset, selectedFolder)
          : searchCompiledKnowledge(keywords, selected, offset));
        if (generation !== searchGeneration.current) return;
        setSearch(result);
        if (!selectedFolder) setFolders(result.folders ?? []);
        if (
          result.items[0] &&
          (selectedFolder || keywords || !result.folders?.length)
        )
          void read(result.items[0], 0);
        else {
          displayedPage.current = null;
          setPage(null);
        }
      } catch (reason) {
        if (generation === searchGeneration.current)
          setError(
            reason instanceof Error ? reason.message : "编译结果加载失败",
          );
      } finally {
        if (generation === searchGeneration.current) setLoading(false);
      }
    },
    [read, readingPosition.capture],
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
  const browsingRoot =
    Boolean(search?.folders?.length) && !folder && !activeQuery;

  return (
    <section
      className="knowledge-config compiled-knowledge"
      aria-label="编译结果与知识点"
      aria-busy={loading || reading}
    >
      <form
        className="compiled-knowledge-toolbar compiled-knowledge-searchbar"
        role="search"
        aria-label="知识点搜索"
        onSubmit={(event) => {
          event.preventDefault();
          void load(sourceId, query, 0, folder);
        }}
      >
        <label>
          <span className="sr-only">搜索知识点</span>
          <input
            type="search"
            maxLength={160}
            value={query}
            placeholder="课程名、知识点或公式关键词"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <Button
          type="submit"
          variant="ghost"
          size="icon"
          aria-label="搜索正文"
          title="搜索正文"
          disabled={loading}
        >
          <Search aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="刷新结果"
          title="刷新结果"
          disabled={loading}
          onClick={() => void refresh()}
        >
          <RefreshCw aria-hidden="true" />
        </Button>
      </form>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <p role="status" className="compiled-knowledge-progress">
        {loading
          ? "正在切换目录，保留当前内容…"
          : reading
            ? "正在读取讲义，保留当前内容…"
            : search
              ? `找到 ${search.count} 份结果${search.truncated ? "（请进入更具体的文件夹搜索）" : ""}`
              : "正在读取编译结果…"}
      </p>
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
      <div
        className="compiled-knowledge-layout"
        data-browsing={browsingRoot ? "root" : "folder"}
        data-navigation={browsingRoot || navigationOpen ? "open" : "closed"}
      >
        <aside
          className="compiled-knowledge-navigation"
          hidden={!browsingRoot && !navigationOpen}
        >
          <nav
            className="compiled-knowledge-folders"
            aria-label="课程与课时文件夹"
          >
            <h3>课程目录</h3>
            <Button
              variant="ghost"
              aria-pressed={!folder}
              onClick={() => {
                const selected = sources.some(
                  (source) => source.id === preferredSource,
                )
                  ? preferredSource
                  : "";
                setSourceId(selected);
                void load(selected, activeQuery);
              }}
            >
              全部课程
            </Button>
            <KnowledgeFolders
              folders={folders}
              selected={folder}
              selectedSource={sourceId}
              onSelect={(source, path) => {
                setSourceId(source);
                void load(source, activeQuery, 0, path);
              }}
            />
          </nav>
          {!browsingRoot && (
            <div
              className="compiled-knowledge-list"
              aria-label="Markdown 结果列表"
            >
              <p
                className="compiled-knowledge-folder-path"
                title={folder || "全部课程"}
              >
                {folder.split("/").at(-1) || "全部课程"}
              </p>
              {search?.items.map((doc) => (
                <Button
                  key={doc.id}
                  variant="ghost"
                  aria-pressed={page?.id === doc.id}
                  className="compiled-knowledge-item"
                  onClick={() => void read(doc)}
                >
                  <strong>
                    <FileText aria-hidden="true" />
                    {documentTitle(doc)}
                  </strong>
                  <span>{documentStatus(doc)}</span>
                  {activeQuery && doc.snippet && <small>{doc.snippet}</small>}
                </Button>
              ))}
              {search?.next_offset != null && (
                <Button
                  variant="outline"
                  onClick={() =>
                    void load(
                      sourceId,
                      activeQuery,
                      search.next_offset ?? 0,
                      folder,
                    )
                  }
                  disabled={loading}
                >
                  下一组结果
                </Button>
              )}
              {search && searchOffset > 0 && (
                <Button
                  variant="ghost"
                  onClick={() => void load(sourceId, activeQuery, 0, folder)}
                  disabled={loading}
                >
                  返回首组结果
                </Button>
              )}
            </div>
          )}
        </aside>
        {!browsingRoot && (
          <div className="compiled-knowledge-reading-pane">
            <div
              className="compiled-knowledge-reader-actions"
              role="toolbar"
              aria-label="阅读操作"
            >
              <Button
                variant="ghost"
                size="icon"
                className="compiled-knowledge-navigation-toggle"
                aria-label={navigationOpen ? "收起课程目录" : "展开课程目录"}
                title={navigationOpen ? "收起课程目录" : "展开课程目录"}
                onClick={() => setNavigationOpen((value) => !value)}
              >
                {navigationOpen ? (
                  <PanelLeftClose
                    aria-hidden="true"
                    size={16}
                    strokeWidth={1.5}
                  />
                ) : (
                  <PanelLeftOpen
                    aria-hidden="true"
                    size={16}
                    strokeWidth={1.5}
                  />
                )}
              </Button>
              {page && (
                <>
                  <span
                    className="compiled-knowledge-reader-title"
                    title={documentTitle(page)}
                  >
                    {documentTitle(page)}
                  </span>
                  <KnowledgeDocumentInfo
                    status={documentStatus(page)}
                    section={page.section}
                    citation={page.citation}
                  />
                  <Button
                    variant="ghost"
                    disabled={reading}
                    onClick={() => void read(page, 0, false, true)}
                  >
                    从头阅读
                  </Button>
                  <Button variant="ghost" onClick={() => void open(page)}>
                    在本地显示
                  </Button>
                </>
              )}
            </div>
            <div
              ref={readingPosition.ref}
              onScroll={readingPosition.onScroll}
              className="compiled-knowledge-reader"
              aria-label="Markdown 正文预览"
              tabIndex={0}
            >
              {page && (
                <>
                  {page.error && (
                    <p role="alert">
                      尚未完成：{page.error}{" "}
                      当前显示已保存原文，不是完整知识稿。
                    </p>
                  )}
                  <div className="rich-preview-markdown">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkMath]}
                      rehypePlugins={[
                        [
                          rehypeKatex,
                          {
                            trust: false,
                            throwOnError: false,
                            maxExpand: 1000,
                            maxSize: 10,
                          },
                        ],
                      ]}
                      skipHtml
                      components={{
                        img: ({ alt }) => (
                          <span>［{alt || "页图"}：请在结果目录查看］</span>
                        ),
                        a: ({ children, href }) =>
                          href?.startsWith("#") ? (
                            <a href={href}>{children}</a>
                          ) : (
                            <span>{children}</span>
                          ),
                        h1: ({ children }) => (
                          <h1
                            id={String(children)
                              .replace(/[–—]/g, "-")
                              .replace(/\s+/g, "-")}
                          >
                            {children}
                          </h1>
                        ),
                      }}
                    >
                      {page.offset === 0
                        ? page.text.replace(
                            /^---\r?\n[\s\S]*?\r?\n---\r?\n/,
                            "",
                          )
                        : page.text}
                    </ReactMarkdown>
                  </div>
                  {page.next_offset != null && (
                    <Button
                      variant="outline"
                      disabled={reading}
                      onClick={() =>
                        void read(page, page.next_offset ?? 0, true)
                      }
                    >
                      继续阅读
                    </Button>
                  )}
                  <p>
                    正文分页预览：已加载{" "}
                    {Math.min(
                      page.offset + knowledgeTextLength(page.text),
                      page.total_chars,
                    )}{" "}
                    / {page.total_chars} 字符
                    {page.next_offset == null
                      ? " · 全文已显示"
                      : " · 继续阅读会追加后文；这不是编译进度"}
                  </p>
                </>
              )}
            </div>
          </div>
        )}
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
