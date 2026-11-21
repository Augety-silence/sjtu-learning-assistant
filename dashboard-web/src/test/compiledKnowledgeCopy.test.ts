import { beforeEach, describe, expect, it, vi } from "vitest";
import { readCompiledKnowledge } from "@/lib/api";
import { completeKnowledgeText } from "@/lib/compiledKnowledgeCopy";

vi.mock("@/lib/api", () => ({ readCompiledKnowledge: vi.fn() }));
const page = { id: "a".repeat(32), content_sha256: "version", total_chars: 6 };
beforeEach(() => vi.clearAllMocks());
describe("completeKnowledgeText", () => {
  it("emoji 和补充平面符号使用后端 Unicode 页码偏移，能完整复制", async () => {
    vi.mocked(readCompiledKnowledge)
      .mockResolvedValueOnce({
        ...page,
        total_chars: 4,
        offset: 0,
        text: "课😀",
        next_offset: 2,
      } as never)
      .mockResolvedValueOnce({
        ...page,
        total_chars: 4,
        offset: 2,
        text: "𝑥程",
        next_offset: null,
      } as never);
    expect(await completeKnowledgeText(page.id)).toBe("课😀𝑥程");
    expect(readCompiledKnowledge).toHaveBeenNthCalledWith(2, page.id, 2);
  });
  it("从零开始读完全部分页，而不是复制当前预览片段", async () => {
    vi.mocked(readCompiledKnowledge)
      .mockResolvedValueOnce({
        ...page,
        offset: 0,
        text: "第一段",
        next_offset: 3,
      } as never)
      .mockResolvedValueOnce({
        ...page,
        offset: 3,
        text: "第二段",
        next_offset: null,
      } as never);
    expect(await completeKnowledgeText(page.id)).toBe("第一段第二段");
    expect(readCompiledKnowledge).toHaveBeenNthCalledWith(1, page.id, 0);
    expect(readCompiledKnowledge).toHaveBeenNthCalledWith(2, page.id, 3);
  });
  it("版本改变或短页终止时不交付混合或不完整的讲义", async () => {
    vi.mocked(readCompiledKnowledge)
      .mockResolvedValueOnce({
        ...page,
        offset: 0,
        text: "第一段",
        next_offset: 3,
      } as never)
      .mockResolvedValueOnce({
        ...page,
        content_sha256: "changed",
        offset: 3,
        text: "第二段",
        next_offset: null,
      } as never);
    await expect(completeKnowledgeText(page.id)).rejects.toThrow("版本已变化");
    vi.mocked(readCompiledKnowledge).mockResolvedValueOnce({
      ...page,
      offset: 0,
      text: "第一段",
      next_offset: null,
    } as never);
    await expect(completeKnowledgeText(page.id)).rejects.toThrow("未完整读取");
  });
  it("切换文档取消在途复制，不继续读下一页", async () => {
    let active = true;
    vi.mocked(readCompiledKnowledge).mockImplementationOnce(async () => {
      active = false;
      return { ...page, offset: 0, text: "第一段", next_offset: 3 } as never;
    });
    await expect(completeKnowledgeText(page.id, () => active)).rejects.toThrow(
      "已切换文档",
    );
    expect(readCompiledKnowledge).toHaveBeenCalledOnce();
  });
});
