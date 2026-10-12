// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import {
  TranscriptLearningFlow,
  TranscriptPractice,
} from "@/components/TranscriptPhase1Panels";
import { cleanup, render, screen } from "@/test/render";

afterEach(cleanup);
const legacy = JSON.stringify({
  lesson_topic: "合成课程",
  learning_objectives: [],
  timeline: [],
  concepts: [{ text: "边际贡献用于覆盖固定成本。", evidence: ["cue-1"] }],
  emphasized_points: [
    { text: "区分固定成本与变动成本。", evidence: ["cue-2"] },
  ],
  cases_formulas_conclusions: [
    { text: "边际贡献 = 收入 - 变动成本。", evidence: ["cue-3"] },
  ],
  review_questions: ["如何计算单位边际贡献？"],
});
describe("真实旧版字幕小结合同兼容", () => {
  it("renders the production concept/statement and structured cue evidence contract", () => {
    const content = JSON.stringify({
      knowledge_points: [
        {
          concept: "边际贡献",
          statement: "收入减去变动成本。",
          kind: "formula",
          evidence: [
            {
              cue_id: "cue-4",
              start_ms: 5000,
              end_ms: 10000,
              quote: "每件边际贡献40元",
            },
          ],
        },
      ],
      classroom_examples: [{ example: "课堂案例", evidence: [] }],
      cases_formulas_conclusions: [{ text: "旧版公式仍需保留", evidence: [] }],
    });
    render(<TranscriptLearningFlow content={content} />);
    expect(screen.getByText("边际贡献")).toBeTruthy();
    expect(screen.getByText(/收入减去变动成本/)).toBeTruthy();
    expect(screen.getByText(/cue-4 · 00:05–00:10/)).toBeTruthy();
    expect(screen.getByText("课堂案例")).toBeTruthy();
    expect(screen.getByText("旧版公式仍需保留")).toBeTruthy();
  });

  it("renders legacy concepts, emphasis and mixed cases/formulas with source locators", () => {
    render(<TranscriptLearningFlow content={legacy} />);
    expect(screen.getByText(/边际贡献用于覆盖固定成本/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "课堂强调" })).toBeTruthy();
    expect(screen.getByText(/区分固定成本与变动成本/)).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "案例、公式与结论" }),
    ).toBeTruthy();
    expect(screen.getByText(/字幕证据：cue-3/)).toBeTruthy();
    expect(screen.queryByText(/本节暂未整理出知识点/)).toBeNull();
  });
  it("makes legacy review questions usable without inventing an answer", () => {
    render(<TranscriptPractice content={legacy} />);
    expect(screen.getByText("【复习自测】")).toBeTruthy();
    expect(screen.getByText("如何计算单位边际贡献？")).toBeTruthy();
    expect(screen.getByText("本题暂未提供参考答案。")).toBeTruthy();
    expect(screen.queryByText("查看答案")).toBeNull();
  });
  it("keeps modern evidence and top-level learning content when study_guide is present", () => {
    render(
      <TranscriptLearningFlow
        content={JSON.stringify({
          knowledge_points: ["新版知识点"],
          classroom_examples: ["新版课堂例子"],
          study_guide: {
            questions: [{ prompt: "现代练习", answer: "参考答案" }],
          },
        })}
      />,
    );
    expect(screen.getByText("新版知识点")).toBeTruthy();
    expect(screen.getByText("新版课堂例子")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "课堂例子" })).toBeTruthy();
  });
});
