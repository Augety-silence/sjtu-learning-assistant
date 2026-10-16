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

const HTML_NAMESPACE = "http://www.w3.org/1999/xhtml";

const allowedPreviewTags = new Set([
  "a",
  "abbr",
  "b",
  "blockquote",
  "br",
  "caption",
  "code",
  "dd",
  "del",
  "div",
  "dl",
  "dt",
  "em",
  "figcaption",
  "figure",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "i",
  "img",
  "ins",
  "kbd",
  "li",
  "mark",
  "ol",
  "p",
  "pre",
  "s",
  "small",
  "span",
  "strong",
  "sub",
  "sup",
  "table",
  "tbody",
  "td",
  "tfoot",
  "th",
  "thead",
  "tr",
  "u",
  "ul",
]);

// Dropped together with their children; everything else outside the allowlist is unwrapped.
const droppedPreviewTags = new Set([
  "base",
  "button",
  "embed",
  "form",
  "frame",
  "frameset",
  "head",
  "iframe",
  "input",
  "link",
  "math",
  "meta",
  "noembed",
  "noframes",
  "noscript",
  "object",
  "option",
  "plaintext",
  "script",
  "select",
  "style",
  "svg",
  "template",
  "textarea",
  "title",
  "xmp",
]);

function safeUrl(value: string) {
  // URL parsers ignore tabs, newlines and leading control characters, so compare without them.
  let normalized = "";
  for (const char of value) {
    if (char.charCodeAt(0) > 0x20) normalized += char;
  }
  normalized = normalized.toLowerCase();
  return (
    !normalized.startsWith("javascript:") &&
    !normalized.startsWith("vbscript:") &&
    !normalized.startsWith("data:text/html")
  );
}

function hasAllowedProtocol(value: string, protocols: readonly string[]) {
  try {
    return protocols.includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

function copySafePreviewNode(node: Node, output: Document): Node | null {
  if (node.nodeType === Node.TEXT_NODE) {
    return output.createTextNode(node.textContent ?? "");
  }
  if (!(node instanceof Element)) return null;

  const tag = node.localName;
  if (node.namespaceURI !== HTML_NAMESPACE || droppedPreviewTags.has(tag)) {
    return null;
  }

  if (!allowedPreviewTags.has(tag)) {
    const fragment = output.createDocumentFragment();
    for (const child of Array.from(node.childNodes)) {
      const safeChild = copySafePreviewNode(child, output);
      if (safeChild) fragment.append(safeChild);
    }
    return fragment;
  }

  const safeElement = output.createElement(tag);
  const title = node.getAttribute("title");
  if (title !== null) safeElement.setAttribute("title", title);
  if (tag === "a") {
    const href = node.getAttribute("href")?.trim() ?? "";
    if (hasAllowedProtocol(href, ["https:", "http:", "mailto:"])) {
      safeElement.setAttribute("href", href);
      safeElement.setAttribute("target", "_blank");
      safeElement.setAttribute("rel", "noreferrer noopener");
    }
  } else if (tag === "img") {
    const src = node.getAttribute("src")?.trim() ?? "";
    if (
      hasAllowedProtocol(src, ["https:"]) ||
      /^data:image\/(?:png|jpe?g|gif|webp|avif);base64,/i.test(src)
    ) {
      safeElement.setAttribute("src", src);
    }
    for (const name of ["alt", "width", "height"]) {
      const value = node.getAttribute(name);
      if (value !== null) safeElement.setAttribute(name, value);
    }
  } else if (tag === "td" || tag === "th") {
    for (const name of ["colspan", "rowspan"]) {
      const value = node.getAttribute(name);
      if (value && /^\d{1,3}$/.test(value)) {
        safeElement.setAttribute(name, value);
      }
    }
  } else if (tag === "ol") {
    const start = node.getAttribute("start");
    if (start && /^-?\d{1,6}$/.test(start)) {
      safeElement.setAttribute("start", start);
    }
  }

  for (const child of Array.from(node.childNodes)) {
    const safeChild = copySafePreviewNode(child, output);
    if (safeChild) safeElement.append(safeChild);
  }
  return safeElement;
}

export function sanitizePreviewHtml(markup: string) {
  if (typeof DOMParser === "undefined") {
    // Without a DOM parser there is no safe way to keep markup; show it as text.
    return markup.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
  }

  // Rebuild an allowlisted tree in a fresh document instead of mutating the parsed one,
  // so parser-differential tricks (noscript, svg/math, SMIL, srcdoc, formaction) cannot survive.
  const parsed = new DOMParser().parseFromString(markup, "text/html");
  const output = document.implementation.createHTMLDocument("");
  const container = output.createElement("div");
  for (const child of Array.from(parsed.body.childNodes)) {
    const safeChild = copySafePreviewNode(child, output);
    if (safeChild) container.append(safeChild);
  }
  return container.innerHTML;
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
