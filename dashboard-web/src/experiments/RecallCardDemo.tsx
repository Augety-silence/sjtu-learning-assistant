import { useState } from "react";
import {
  RecallCard,
  type RecallCardVariant,
} from "@/components/experiments/RecallCard";
import type { WidgetMotionMode } from "@/components/experiments/widgetMotion";
import { CallbackLog, DemoCase } from "./DemoFrame";

// SYNTHETIC DATA — fabricated for the widget lab only; not real course content.
const SAMPLE = {
  question:
    "【合成示例】若 T(n) = 2T(n/2) + n，递归树共有多少层？每层代价是多少？",
  answer: "【合成示例】约 log n 层，每层代价为 n，因此总代价为 Θ(n log n)。",
  sourceLabel: "合成讲义 · 第 3 讲",
};
const LONG = {
  question:
    "【合成长文本】请说明主定理三种情形各自的判定条件，并分别举一个满足该情形的递推式；如果递推式不满足主定理的正则性条件，应当如何处理？Supercalifragilisticexpialidocious_without_spaces_to_test_overflow_wrap。",
  answer:
    "【合成长文本】情形一：f(n) 多项式地小于 n^{log_b a}；情形二：两者同阶；情形三：f(n) 多项式地大于 n^{log_b a} 且满足正则性条件。\n不满足正则性时可以改用递归树或 Akra–Bazzi 方法逐层估计。\n（本段仅用于检查换行、窄屏和高度变化。）",
};

function Case({
  mode,
  variant,
  question = SAMPLE.question,
  answer = SAMPLE.answer,
  sourceLabel = SAMPLE.sourceLabel,
}: {
  mode: WidgetMotionMode;
  variant: RecallCardVariant;
  question?: string | null;
  answer?: string | null;
  sourceLabel?: string | null;
}) {
  const [revealed, setRevealed] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  return (
    <>
      <RecallCard
        motion={mode}
        variant={variant}
        question={question}
        answer={answer}
        sourceLabel={sourceLabel}
        revealed={revealed}
        onRevealChange={(next) => {
          setRevealed(next);
          setLog((items) => [...items, `onRevealChange(${next})`]);
        }}
      />
      <CallbackLog entries={log} />
    </>
  );
}

/** Host keeps one card revealed at a time — shows the controlled contract. */
function ListCase({ mode }: { mode: WidgetMotionMode }) {
  const items = [
    { id: "a", q: "【合成】归并排序的时间复杂度？", a: "【合成】Θ(n log n)。" },
    { id: "b", q: "【合成】快速排序最坏情况何时出现？", a: null },
    { id: "c", q: "【合成】堆排序是否稳定？", a: "【合成】不稳定。" },
  ];
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div className="widget-lab-stack">
      {items.map((item) => (
        <RecallCard
          key={item.id}
          motion={mode}
          question={item.q}
          answer={item.a}
          revealed={openId === item.id}
          onRevealChange={(next) => setOpenId(next ? item.id : null)}
        />
      ))}
    </div>
  );
}

export function RecallCardDemo() {
  return (
    <>
      <DemoCase
        title="A. 局部展开（推荐）"
        note="题目在上，答案在下方展开；隐藏时答案不在 DOM 中，读屏和 Tab 都到不了。"
      >
        {(mode) => <Case mode={mode} variant="expand" />}
      </DemoCase>
      <DemoCase
        title="B. 原位交叉淡入"
        note="占位提示与答案在同一位置交替；退场中的节点会被设为 inert。"
      >
        {(mode) => <Case mode={mode} variant="crossfade" />}
      </DemoCase>
      <DemoCase
        title="缺字段：无答案、无题目"
        note="如实提示“未提供参考答案 / 未提供题目”，不生成任何内容。"
      >
        {(mode) => (
          <>
            <Case mode={mode} variant="expand" answer={null} />
            <Case
              mode={mode}
              variant="crossfade"
              question="   "
              answer={undefined}
              sourceLabel={null}
            />
          </>
        )}
      </DemoCase>
      <DemoCase title="长文本 / 窄屏">
        {(mode) => (
          <Case
            mode={mode}
            variant="expand"
            question={LONG.question}
            answer={LONG.answer}
            sourceLabel="合成讲义 · 一个非常非常长的来源标签用于测试截断与省略号显示效果 · 第 12 讲"
          />
        )}
      </DemoCase>
      <DemoCase
        title="受控列表：一次只展开一张"
        note="展开状态完全由宿主决定；组件本身不记录“已学会”或正确率。"
      >
        {(mode) => <ListCase mode={mode} />}
      </DemoCase>
    </>
  );
}
