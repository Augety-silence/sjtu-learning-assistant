import { Children, isValidElement, memo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { remarkExecutionStrong } from "@/lib/executionMarkdown";
import { parseMessageLink } from "@/lib/messageLink";
import { rehypeStreamFade, type useStreamFade } from "@/lib/streamReveal";

function safeMarkdownUrl(url: string, key: string) {
  const value = url.trim();
  if (key === "href" && parseMessageLink(value)) return value;
  if (key === "href" && /^(https?:\/\/|mailto:)/i.test(value)) return value;
  if (key !== "href" && /^https?:\/\//i.test(value)) return value;
  return null;
}

// 旧 WKWebView 不支持 GFM 自动链接内部的 lookbehind，降级 CommonMark。
export function safeRemarkPlugins() {
  try {
    new RegExp("(?<=a)b");
    return [remarkGfm];
  } catch {
    return [];
  }
}
const remarkPlugins = safeRemarkPlugins();
const executionPlugins = [...remarkPlugins, remarkExecutionStrong];

function NumberedCodeBlock({ children }: { children?: ReactNode }) {
  const child = Children.count(children) === 1 ? Children.only(children) : null;
  if (!isValidElement<{ children?: ReactNode; className?: string }>(child))
    return <pre>{children}</pre>;
  const source = String(child.props.children ?? "").replace(/\n$/, "");
  return (
    <pre className="ai-code-with-lines">
      <code className={child.props.className}>
        {source.split("\n").map((line, index) => (
          <span className="ai-code-line" key={`${index}-${line}`}>
            <span className="ai-code-line-number" aria-hidden="true">
              {index + 1}
            </span>
            <span>{line || " "}</span>
          </span>
        ))}
      </code>
    </pre>
  );
}

// 主答案和执行说明共用相同的清洗、链接和远程图片规则。
export const MessageMarkdown = memo(function MessageMarkdown({
  content,
  showCodeLineNumbers = false,
  clip = false,
  fade = null,
  execution = false,
}: {
  content: string;
  showCodeLineNumbers?: boolean;
  clip?: boolean;
  fade?: ReturnType<typeof useStreamFade>;
  execution?: boolean;
}) {
  const markdown = (
    <ReactMarkdown
      remarkPlugins={execution ? executionPlugins : remarkPlugins}
      rehypePlugins={
        fade ? [rehypeSanitize, [rehypeStreamFade, fade]] : [rehypeSanitize]
      }
      urlTransform={safeMarkdownUrl}
      components={{
        a: ({ node: _node, ...props }) =>
          parseMessageLink(props.href ?? "") ? (
            <a
              {...props}
              onClick={(event) => {
                event.preventDefault();
                window.dispatchEvent(
                  new CustomEvent("sjtu-open-message", { detail: props.href }),
                );
              }}
            />
          ) : (
            <a {...props} target="_blank" rel="noreferrer" />
          ),
        // 不自动请求模型输出中的图片地址，避免资料通过 URL 外泄。
        img: ({ src, alt }) => (
          <span title={typeof src === "string" ? src : undefined}>
            [图片：{alt || "外部图片"}]
          </span>
        ),
        table: ({ node: _node, ...props }) => (
          <div
            className="ai-markdown-table"
            role="region"
            aria-label="表格内容"
            tabIndex={0}
          >
            <table {...props} />
          </div>
        ),
        ...(showCodeLineNumbers
          ? {
              pre: ({ node: _node, children }) => (
                <NumberedCodeBlock>{children}</NumberedCodeBlock>
              ),
            }
          : {}),
      }}
    >
      {content}
    </ReactMarkdown>
  );
  return (
    <div className="ai-markdown">
      {clip ? <div className="ai-markdown-clip">{markdown}</div> : markdown}
    </div>
  );
});
