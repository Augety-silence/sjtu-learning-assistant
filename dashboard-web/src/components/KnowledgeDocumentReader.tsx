import type { RefObject } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
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
