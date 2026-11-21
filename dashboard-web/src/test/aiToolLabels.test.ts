import { describe, expect, it } from "vitest";
import { toolLabel } from "@/components/AIExecutionCard";

describe("toolLabel", () => {
  it("把课件正文读取工具显示为中文动作而不是内部名称", () => {
    expect(toolLabel("read_course_file_text")).toBe("读取课件正文");
    expect(toolLabel("read_ai_attachment_text")).toBe("读取附件正文");
    expect(toolLabel("search_compiled_knowledge")).toBe("检索编译知识点");
    expect(toolLabel("read_compiled_knowledge")).toBe("读取编译知识正文");
    expect(toolLabel("unknown_tool")).toBe("unknown_tool");
  });
});
