const ALLOWED_TAGS = new Set([
  "p",
  "br",
  "div",
  "ul",
  "ol",
  "li",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "b",
  "strong",
  "i",
  "em",
  "u",
  "pre",
  "code",
  "blockquote",
  "a",
  "img",
]);
const DROP_WITH_CONTENT = new Set([
  "script",
  "style",
  "iframe",
  "object",
  "embed",
  "template",
  "noscript",
]);

export function isHttpsUrl(value: string) {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function copyPresentation(node: Element, safe: HTMLElement, tag: string) {
  if (["table", "td", "th", "div", "p"].includes(tag)) {
    const align = node.getAttribute("align")?.toLowerCase();
    if (align && ["left", "center", "right", "justify"].includes(align)) {
      safe.setAttribute("align", align);
    }
  }
  if (["td", "th"].includes(tag)) {
    for (const name of ["colspan", "rowspan"]) {
      const value = node.getAttribute(name) ?? "";
      if (/^\d{1,3}$/.test(value) && +value >= 1 && +value <= 100) {
        safe.setAttribute(name, String(+value));
      }
    }
    const valign = node.getAttribute("valign");
    if (valign && ["top", "middle", "bottom"].includes(valign)) {
      safe.setAttribute("valign", valign);
    }
  }
  if (["table", "img"].includes(tag)) {
    for (const name of tag === "img" ? ["width", "height"] : ["width"]) {
      const value = node.getAttribute(name) ?? "";
      if (/^\d{1,4}$/.test(value) && +value >= 1 && +value <= 2048) {
        safe.setAttribute(name, String(+value));
      } else if (
        name === "width" &&
        /^\d{1,3}%$/.test(value) &&
        +value.slice(0, -1) >= 1 &&
        +value.slice(0, -1) <= 100
      ) {
        safe.setAttribute(name, value);
      }
    }
  }
  if (tag === "table") {
    const role = node.getAttribute("role");
    if (role === "presentation" || role === "table")
      safe.setAttribute("role", role);
    const border = node.getAttribute("border");
    if (border === "0" || border === "1") safe.setAttribute("border", border);
  }
}

function copySafeNode(node: Node, output: Document): Node | null {
  if (node.nodeType === Node.TEXT_NODE)
    return output.createTextNode(node.textContent ?? "");
  if (!(node instanceof Element)) return null;
  const tag = node.tagName.toLowerCase();
  if (DROP_WITH_CONTENT.has(tag)) return null;
  if (!ALLOWED_TAGS.has(tag)) {
    const fragment = output.createDocumentFragment();
    for (const child of Array.from(node.childNodes)) {
      const safeChild = copySafeNode(child, output);
      if (safeChild) fragment.append(safeChild);
    }
    return fragment;
  }
  const safe = output.createElement(tag);
  if (tag === "a") {
    const href = node.getAttribute("href");
    if (href && isHttpsUrl(href)) safe.setAttribute("href", href);
    else if (href) {
      const blocked = output.createElement("span");
      blocked.setAttribute("data-link-blocked", "true");
      for (const child of Array.from(node.childNodes)) {
        const safeChild = copySafeNode(child, output);
        if (safeChild) blocked.append(safeChild);
      }
      blocked.append(output.createTextNode("（已拦截链接）"));
      return blocked;
    }
  } else if (tag === "img") {
    const resourceId = node.getAttribute("data-resource-id");
    const alt = node.getAttribute("alt");
    if (!resourceId) {
      return output.createTextNode(
        node.getAttribute("data-blocked-image") === "true"
          ? `[外部图片已拦截${alt ? `：${alt}` : "，请在原邮箱查看"}]`
          : alt
            ? `[图片：${alt}]`
            : "[无可用来源的图片]",
      );
    }
    safe.setAttribute("data-resource-id", resourceId);
    if (alt !== null) safe.setAttribute("alt", alt);
  }
  copyPresentation(node, safe, tag);
  for (const child of Array.from(node.childNodes)) {
    const safeChild = copySafeNode(child, output);
    if (safeChild) safe.append(safeChild);
  }
  return safe;
}

export function sanitizeMessageHtml(html: string, email = false) {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const output = document.implementation.createHTMLDocument("");
  const container = output.createElement("div");
  for (const child of Array.from(parsed.body.childNodes)) {
    const safeChild = copySafeNode(child, output);
    if (safeChild) container.append(safeChild);
  }
  for (const table of Array.from(container.querySelectorAll("table"))) {
    if (
      table.getAttribute("role") === "table" ||
      table.getAttribute("border") === "1"
    )
      continue;
    if (
      table.getAttribute("role") === "presentation" ||
      table.getAttribute("border") === "0" ||
      (email && !table.querySelector("th"))
    ) {
      table.setAttribute("role", "presentation");
      table.setAttribute("data-layout-table", "true");
    }
  }
  return container.innerHTML;
}
