import type { RefObject } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { ContentSearch, useSearchHighlight } from "@/components/ContentSearch";
import { Button } from "@/components/ui/Button";
import type { CompiledKnowledgePage } from "@/lib/types";

export function KnowledgeDocumentReader({
  page,
  reading,
  position,
  onContinue,
}: {
  page: CompiledKnowledgePage | null;
  reading: boolean;
  position: { ref: RefObject<HTMLDivElement | null>; onScroll: () => void };
  onContinue: () => void;
}) {
  return (
    <ContentSearch
      resetKey={page?.id}
      className="knowledge-reader-search"
      note={
        page?.next_offset != null
          ? "搜索已加载正文，继续阅读后会包含后续内容。"
          : undefined
      }
    >
      <KnowledgeDocumentBody
        page={page}
        reading={reading}
        position={position}
        onContinue={onContinue}
      />
    </ContentSearch>
  );
}

function KnowledgeDocumentBody({
  page,
  reading,
  position,
  onContinue,
}: Parameters<typeof KnowledgeDocumentReader>[0]) {
  const highlight = useSearchHighlight();
  return (
    <div
      ref={position.ref}
      onScroll={position.onScroll}
      className="compiled-knowledge-reader"
      aria-label="Markdown 正文预览"
      tabIndex={0}
    >
      {page && (
        <>
          {page.error && (
            <p role="alert">
              尚未完成：{page.error} 当前显示已保存原文，不是完整知识稿。
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
                highlight,
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
                ? page.text.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "")
                : page.text}
            </ReactMarkdown>
          </div>
          {page.next_offset != null && (
            <Button
              variant="outline"
              disabled={reading}
              onClick={() => onContinue()}
            >
              继续阅读
            </Button>
          )}
        </>
      )}
    </div>
  );
}
