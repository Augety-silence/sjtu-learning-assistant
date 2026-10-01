export type PreviewerKind =
  | "pdf"
  | "image"
  | "markdown"
  | "text"
  | "code"
  | "unsupported";

export interface PreviewerRule {
  kind: Exclude<PreviewerKind, "unsupported">;
  mimeTypes?: readonly string[];
  mimePrefixes?: readonly string[];
  extensions?: readonly string[];
}

const IMAGE_EXTENSIONS = [
  "avif",
  "bmp",
  "gif",
  "jpeg",
  "jpg",
  "png",
  "svg",
  "webp",
] as const;
const MARKDOWN_EXTENSIONS = ["markdown", "md", "mdown", "mkd"] as const;
const CODE_EXTENSIONS = [
  "c",
  "cc",
  "cpp",
  "css",
  "go",
  "h",
  "hpp",
  "html",
  "ipynb",
  "java",
  "js",
  "json",
  "jsx",
  "kt",
  "kts",
  "mjs",
  "py",
  "rb",
  "rs",
  "sh",
  "sql",
  "toml",
  "ts",
  "tsx",
  "vue",
  "xml",
  "yaml",
  "yml",
] as const;
const TEXT_EXTENSIONS = ["csv", "log", "rtf", "text", "tsv", "txt"] as const;
const BINARY_DOCUMENT_EXTENSIONS = new Set([
  "doc",
  "docm",
  "docx",
  "odp",
  "ods",
  "odt",
  "pot",
  "potx",
  "ppt",
  "pptm",
  "pptx",
  "xls",
  "xlsb",
  "xlsm",
  "xlsx",
]);

export const previewerRegistry: readonly PreviewerRule[] = [
  { kind: "pdf", mimeTypes: ["application/pdf"], extensions: ["pdf"] },
  { kind: "image", mimePrefixes: ["image/"], extensions: IMAGE_EXTENSIONS },
  {
    kind: "markdown",
    mimeTypes: ["text/markdown", "text/x-markdown"],
    extensions: MARKDOWN_EXTENSIONS,
  },
  {
    kind: "code",
    mimeTypes: [
      "application/javascript",
      "application/json",
      "application/toml",
      "application/typescript",
      "application/x-httpd-php",
      "application/x-sh",
      "application/xhtml+xml",
      "application/xml",
      "application/yaml",
      "text/css",
      "text/html",
      "text/javascript",
      "text/typescript",
      "text/x-c",
      "text/x-c++",
      "text/x-java-source",
      "text/x-python",
      "text/x-shellscript",
      "text/xml",
      "text/yaml",
    ],
    extensions: CODE_EXTENSIONS,
  },
  { kind: "text", mimePrefixes: ["text/"], extensions: TEXT_EXTENSIONS },
] as const;

function normalizedMimeType(mimeType?: string | null): string {
  return mimeType?.split(";", 1)[0].trim().toLowerCase() || "";
}

function fileExtension(fileName?: string | null): string {
  const baseName = fileName?.split(/[\\/]/).pop()?.toLowerCase() || "";
  const dot = baseName.lastIndexOf(".");
  return dot > 0 && dot < baseName.length - 1 ? baseName.slice(dot + 1) : "";
}

function matchesMime(rule: PreviewerRule, mimeType: string): boolean {
  return Boolean(
    mimeType &&
      (rule.mimeTypes?.includes(mimeType) ||
        rule.mimePrefixes?.some((prefix) => mimeType.startsWith(prefix))),
  );
}

export function getPreviewerKind(
  mimeType?: string | null,
  fileName?: string | null,
): PreviewerKind {
  const mime = normalizedMimeType(mimeType);
  const extension = fileExtension(fileName);
  if (BINARY_DOCUMENT_EXTENSIONS.has(extension)) return "unsupported";
  const mimeMatch = previewerRegistry.find((rule) => matchesMime(rule, mime));
  if (mimeMatch?.kind !== "text")
    return mimeMatch?.kind || matchExtension(extension);
  const extensionMatch = matchExtension(extension);
  return extensionMatch === "unsupported" ? "text" : extensionMatch;
}

function matchExtension(extension: string): PreviewerKind {
  if (!extension) return "unsupported";
  return (
    previewerRegistry.find((rule) => rule.extensions?.includes(extension))
      ?.kind || "unsupported"
  );
}

export const selectPreviewer = getPreviewerKind;
