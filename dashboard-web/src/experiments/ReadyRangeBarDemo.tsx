import { Pause, Play, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import {
  type RangeMs,
  ReadyRangeBar,
} from "@/components/experiments/ReadyRangeBar";
import type { WidgetMotionMode } from "@/components/experiments/widgetMotion";
import { formatClockMs } from "@/components/experiments/widgetMotion";
import { Button } from "@/components/ui/Button";
import { CallbackLog, DemoCase } from "./DemoFrame";

// SYNTHETIC DATA — fabricated for the widget lab only.
const DURATION_MS = 45 * 60_000;
const INCREMENTAL_RANGES: RangeMs[] = [
  [0, 4 * 60_000],
  [4 * 60_000, 9 * 60_000],
  [12 * 60_000, 18 * 60_000],
  [21 * 60_000, 26 * 60_000],
  [30 * 60_000, 33 * 60_000],
];
const MESSY_RANGES: RangeMs[] = [
  [-30_000, 60_000],
  [50_000, 3 * 60_000],
  [10 * 60_000, 8 * 60_000],
  [Number.NaN, 5 * 60_000],
  [20 * 60_000, 20 * 60_000],
  [40 * 60_000, 99 * 60_000],
];

function PlaybackCase({ mode }: { mode: WidgetMotionMode }) {
  const [current, setCurrent] = useState(2 * 60_000);
  const [count, setCount] = useState(2);
  const [playing, setPlaying] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setCurrent((value) => (value + 30_000) % DURATION_MS);
    }, 500);
    return () => window.clearInterval(timer);
  }, [playing]);
  return (
    <>
      <ReadyRangeBar
        motion={mode}
        durationMs={DURATION_MS}
        currentTimeMs={current}
        availableRangesMs={INCREMENTAL_RANGES.slice(0, count)}
        onSeekMs={(ms) => {
          setCurrent(ms);
          setLog((items) => [...items, `onSeekMs(${formatClockMs(ms)})`]);
        }}
      />
      <div className="widget-lab-toolbar">
        <Button
          size="sm"
          variant="outline"
          onClick={() => setPlaying((v) => !v)}
        >
          {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
          {playing ? "暂停模拟播放" : "模拟播放"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={count >= INCREMENTAL_RANGES.length}
          onClick={() => setCount((v) => v + 1)}
        >
          追加一段材料
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setCount(2);
            setCurrent(2 * 60_000);
            setLog([]);
          }}
        >
          <RotateCcw aria-hidden="true" />
          重置
        </Button>
      </div>
      <CallbackLog entries={log} />
    </>
  );
}

function RapidCase({ mode }: { mode: WidgetMotionMode }) {
  const [tick, setTick] = useState(0);
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setTick((v) => v + 1), 120);
    return () => window.clearInterval(timer);
  }, [running]);
  const end = Math.min(DURATION_MS, (tick % 60) * 45_000 + 60_000);
  return (
    <>
      <ReadyRangeBar
        motion={mode}
        durationMs={DURATION_MS}
        currentTimeMs={(tick * 20_000) % DURATION_MS}
        availableRangesMs={[
          [0, end],
          [end + 60_000, end + 120_000],
        ]}
        onSeekMs={(ms) =>
          setLog((items) => [...items, `onSeekMs(${formatClockMs(ms)})`])
        }
      />
      <div className="widget-lab-toolbar">
        <Button
          size="sm"
          variant="outline"
          onClick={() => setRunning((v) => !v)}
        >
          {running ? "停止快速更新" : "开始每 120ms 更新 props"}
        </Button>
      </div>
      <CallbackLog entries={log} />
    </>
  );
}

export function ReadyRangeBarDemo() {
  return (
    <>
      <DemoCase
        title="正常：播放中，材料增量出现"
        note="点击或用方向键选定位置后，需点“确认跳转”或按 Enter 才会调用 onSeekMs。"
      >
        {(mode) => <PlaybackCase mode={mode} />}
      </DemoCase>
      <DemoCase title="空数据：尚无任何材料区间">
        {(mode) => (
          <ReadyRangeBar
            motion={mode}
            durationMs={DURATION_MS}
            currentTimeMs={5 * 60_000}
            availableRangesMs={[]}
            onSeekMs={() => undefined}
          />
        )}
      </DemoCase>
      <DemoCase
        title="异常输入：重叠、越界、倒置、NaN、零长度"
        note="输入 6 段，规范化后只剩 2 段：[00:00–03:00] 与 [40:00–45:00]。"
      >
        {(mode) => (
          <ReadyRangeBar
            motion={mode}
            durationMs={DURATION_MS}
            currentTimeMs={99 * 60_000}
            availableRangesMs={MESSY_RANGES}
          />
        )}
      </DemoCase>
      <DemoCase
        title="缺字段：时长未知、播放位置缺失"
        note="不绘制比例时间带，只列出区间；候选位置同样需要确认。"
      >
        {(mode) => (
          <ReadyRangeBar
            motion={mode}
            durationMs={null}
            currentTimeMs={undefined}
            availableRangesMs={INCREMENTAL_RANGES.slice(0, 3)}
            onSeekMs={() => undefined}
          />
        )}
      </DemoCase>
      <DemoCase
        title="快速更新：props 高频变化"
        note="快速更新期间不会自动跳转；回调记录应保持为空，除非手动确认。"
      >
        {(mode) => <RapidCase mode={mode} />}
      </DemoCase>
    </>
  );
}
