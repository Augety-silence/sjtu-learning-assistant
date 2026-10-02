import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

export type RichPreviewKind =
  | "markdown"
  | "html"
  | "text"
  | "code"
  | "image"
  | "pdf"
  | "unsupported";

export interface RichPreviewContentProps {
  kind: RichPreviewKind;
  title?: string;
  content?: string | null;
  sourceUrl?: string | null;
  alt?: string;
  language?: string;
  emptyLabel?: string;
}

const blockedElements = new Set([
  "base",
  "embed",
  "form",
  "iframe",
  "link",
  "meta",
  "object",
  "script",
  "style",
]);

function safeUrl(value: string) {
  const normalized = value.trim().toLowerCase();
  return (
    !normalized.startsWith("javascript:") &&
    !normalized.startsWith("data:text/html")
  );
}

export function sanitizePreviewHtml(markup: string) {
  if (typeof DOMParser === "undefined") {
    return markup
      .replace(
        /<(script|style|iframe|object|embed|form|meta|link|base)\b[^>]*>[\s\S]*?<\/\1>/gi,
        "",
      )
      .replace(
        /<(script|style|iframe|object|embed|form|meta|link|base)\b[^>]*\/?\s*>/gi,
        "",
      )
      .replace(/\son\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "")
      .replace(/\sstyle\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "")
      .replace(
        /\s(?:href|src)\s*=\s*(["'])\s*(?:javascript:|data:text\/html)[\s\S]*?\1/gi,
        "",
      );
  }

  const document = new DOMParser().parseFromString(markup, "text/html");
  for (const element of Array.from(document.body.querySelectorAll("*"))) {
    if (blockedElements.has(element.tagName.toLowerCase())) {
      element.remove();
      continue;
    }
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (
        name.startsWith("on") ||
        name === "style" ||
        (["href", "src", "xlink:href"].includes(name) &&
          !safeUrl(attribute.value))
      ) {
        element.removeAttribute(attribute.name);
      }
    }
    if (element instanceof HTMLAnchorElement) {
      element.target = "_blank";
      element.rel = "noreferrer noopener";
    }
  }
  return document.body.innerHTML;
}

export function RichPreviewContent({
  kind,
  title = "内容预览",
  content,
  sourceUrl,
  alt,
  language,
  emptyLabel = "暂无可预览内容",
}: RichPreviewContentProps) {
  if (kind === "image" && sourceUrl && safeUrl(sourceUrl)) {
    return (
      <div
        className="material-preview-content"
        role="region"
        aria-label={title}
        tabIndex={0}
      >
        <img src={sourceUrl} alt={alt ?? title} />
      </div>
    );
  }

  if (kind === "pdf" && sourceUrl && safeUrl(sourceUrl)) {
    return (
      <div
        className="material-preview-content"
        role="region"
        aria-label={title}
        tabIndex={0}
      >
        <iframe src={sourceUrl} title={`${title} PDF 预览`} />
      </div>
    );
  }

  if (!content) {
    return (
      <div className="material-preview-unsupported" role="status">
        <strong>{emptyLabel}</strong>
        <span>可以返回列表选择其他内容。</span>
      </div>
    );
  }

  if (kind === "markdown") {
    return (
      <div
        className="ai-markdown"
        role="region"
        aria-label={title}
        tabIndex={0}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeSanitize]}
          components={{
            a: ({ node: _node, ...props }) => (
              <a {...props} target="_blank" rel="noreferrer noopener" />
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
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    );
  }

  if (kind === "html") {
    return (
      <div
        className="message-detail-html"
        role="region"
        aria-label={title}
        tabIndex={0}
        // The source is sanitized locally; scripts, inline handlers and unsafe URLs are removed.
        dangerouslySetInnerHTML={{ __html: sanitizePreviewHtml(content) }}
      />
    );
  }

  if (kind === "code" || kind === "text") {
    return (
      <pre
        className={kind === "code" ? "material-preview-code" : undefined}
        aria-label={language ? `${title}，${language}` : title}
        tabIndex={0}
      >
        {content}
      </pre>
    );
  }

  return (
    <div className="material-preview-unsupported" role="status">
      <strong>暂不支持此格式的内嵌预览</strong>
      <span>{content}</span>
    </div>
  );
}
