import { readCompiledKnowledge } from "@/lib/api";
import type { CompiledKnowledgePage } from "@/lib/types";

/** Backend offsets count Unicode scalar values, not UTF-16 code units. */
export function knowledgeTextLength(text: string) {
  let length = 0;
  for (const _character of text) length += 1;
  return length;
}

/** Read the entire versioned document, never just the visible preview. */
export async function completeKnowledgeText(
  id: string,
  active = () => true,
  first?: CompiledKnowledgePage,
) {
  let offset = 0;
  let total = -1;
  let hash = "";
  const chunks: string[] = [];
  while (active()) {
    const page =
      offset === 0 && first ? first : await readCompiledKnowledge(id, offset);
    if (!active()) throw new Error("已切换文档，取消复制。");
    if (total < 0) {
      total = page.total_chars;
      hash = page.content_sha256 ?? "";
    }
    const length = knowledgeTextLength(page.text);
    if (
      page.id !== id ||
      page.offset !== offset ||
      total < 0 ||
      total > 24 * 1024 * 1024 ||
      !hash ||
      hash !== page.content_sha256 ||
      page.total_chars !== total ||
      offset + length > total ||
      (page.next_offset !== null && page.next_offset !== offset + length)
    )
      throw new Error("讲义版本已变化或分页不完整，请重试复制。");
    chunks.push(page.text);
    offset += length;
    if (page.next_offset === null) {
      if (offset !== total) throw new Error("讲义尚未完整读取，未复制片段。");
      return chunks.join("");
    }
    if (!page.text.length) throw new Error("讲义分页没有前进，未复制片段。");
  }
  throw new Error("已切换文档，取消复制。");
}

export async function copyKnowledgeText(text: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    /* File-backed WebKit may require the selected-text fallback. */
  }
  const input = document.createElement("textarea");
  const focused = document.activeElement as HTMLElement | null;
  input.value = text;
  input.style.cssText =
    "position:fixed;left:0;top:0;opacity:0;pointer-events:none";
  document.body.append(input);
  try {
    input.focus();
    input.select();
    if (!document.execCommand("copy"))
      throw new Error("系统未允许复制，请定位 Markdown 文件后复制。");
  } finally {
    input.remove();
    focused?.focus();
  }
}
