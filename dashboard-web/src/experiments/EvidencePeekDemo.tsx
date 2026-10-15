import { useState } from "react";
import {
  EvidencePeek,
  type EvidencePeekVariant,
} from "@/components/experiments/EvidencePeek";
import type { WidgetMotionMode } from "@/components/experiments/widgetMotion";
import { formatClockMs } from "@/components/experiments/widgetMotion";
import { CallbackLog, DemoCase } from "./DemoFrame";

// SYNTHETIC DATA — fabricated for the widget lab only; not real lecture text.
const SAMPLE = {
  quote:
    "【合成示例】我们把这个递推式展开三次，就能看到每一层的代价都是 n，一共有 log n 层。",
  sourceLabel: "合成字幕 · 第 3 讲",
  startMs: 754_000,
  endMs: 771_500,
};
const LONG_QUOTE =
  "【合成长文本】这里故意放一段很长的字幕引文，用来检查换行、窄屏和浮层宽度：主定理的三种情形分别对应子问题代价占主导、各层代价相当、以及合并代价占主导。在第二种情形中，每一层的代价大致相同，因此总代价等于单层代价乘以层数；当我们讨论更一般的 Akra–Bazzi 形式时，同样的直觉依然成立，只是层数的估计需要更仔细。Supercalifragilisticexpialidocious_without_spaces_to_test_overflow_wrap.";

function Case({
  mode,
  variant,
  quote = SAMPLE.quote,
  sourceLabel = SAMPLE.sourceLabel,
  startMs = SAMPLE.startMs,
  endMs = SAMPLE.endMs,
  seekable = true,
}: {
  mode: WidgetMotionMode;
  variant: EvidencePeekVariant;
  quote?: string | null;
  sourceLabel?: string | null;
  startMs?: number | null;
  endMs?: number | null;
  seekable?: boolean;
}) {
  const [log, setLog] = useState<string[]>([]);
  return (
    <>
      <p className="widget-lab-mock-paragraph">
        知识点：递归树中每层代价相同，总代价为 Θ(n log n)。
        <EvidencePeek
          motion={mode}
          variant={variant}
          quote={quote}
          sourceLabel={sourceLabel}
          startMs={startMs}
          endMs={endMs}
          onSeekMs={
            seekable
              ? (ms) =>
                  setLog((items) => [
                    ...items,
                    `onSeekMs(${formatClockMs(ms)})`,
                  ])
              : undefined
          }
        />
      </p>
      <p className="widget-lab-mock-paragraph">
        （下方的段落用于观察展开时的布局变化。）
      </p>
      <CallbackLog entries={log} />
    </>
  );
}

export function EvidencePeekDemo() {
  return (
    <>
      <DemoCase
        title="A. 内联展开（推荐）"
        note="Tab 聚焦、Enter/空格展开；Escape 收起并把焦点还给触发按钮。"
      >
        {(mode) => <Case mode={mode} variant="inline" />}
      </DemoCase>
      <DemoCase
        title="B. 轻浮层"
        note="不移动焦点、不加遮罩；点外部或焦点离开时收起，Escape 收起并返回焦点。"
      >
        {(mode) => <Case mode={mode} variant="popover" />}
      </DemoCase>
      <DemoCase
        title="缺字段：无来源、无引文、无有效时间"
        note="如实显示“来源未标注 / 未提供字幕引文 / 时间未知”，跳转按钮被替换为说明。"
      >
        {(mode) => (
          <Case
            mode={mode}
            variant="inline"
            quote={null}
            sourceLabel={null}
            startMs={Number.NaN}
            endMs={null}
          />
        )}
      </DemoCase>
      <DemoCase
        title="异常时间：结束早于开始；无跳转回调"
        note="只显示开始时间，不补造结束时间；未传 onSeekMs 时不显示跳转按钮。"
      >
        {(mode) => (
          <Case
            mode={mode}
            variant="popover"
            startMs={90_000}
            endMs={30_000}
            seekable={false}
          />
        )}
      </DemoCase>
      <DemoCase title="长文本 / 窄屏">
        {(mode) => (
          <Case
            mode={mode}
            variant="popover"
            quote={LONG_QUOTE}
            sourceLabel="合成字幕 · 一个非常非常长的来源标签用于测试截断与省略号显示效果 · 第 12 讲"
          />
        )}
      </DemoCase>
    </>
  );
}
