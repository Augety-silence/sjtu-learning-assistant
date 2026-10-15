import { useEffect, useRef, useState } from "react";
import { MaterialUpdateNotice } from "@/components/experiments/MaterialUpdateNotice";
import type { WidgetMotionMode } from "@/components/experiments/widgetMotion";
import { Button } from "@/components/ui/Button";
import { CallbackLog, DemoCase } from "./DemoFrame";

// SYNTHETIC DATA — revisions below are fabricated in-memory; no API requests.

function Heading({
  revision,
  message,
  mode,
  onView,
  autoHideMs,
}: {
  revision: string | number | null;
  message?: string | null;
  mode: WidgetMotionMode;
  onView?: () => void;
  autoHideMs?: number | null;
}) {
  return (
    <div className="widget-lab-mock-heading">
      <span>同步讲义 · 合成课程第 3 讲</span>
      <MaterialUpdateNotice
        motion={mode}
        revision={revision}
        message={message}
        onView={onView}
        autoHideMs={autoHideMs}
      />
    </div>
  );
}

function ManualCase({ mode }: { mode: WidgetMotionMode }) {
  const [revision, setRevision] = useState(1);
  const [renders, setRenders] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const push = (entry: string) => setLog((items) => [...items, entry]);
  return (
    <>
      <Heading
        mode={mode}
        revision={`rev-${revision}`}
        message="【合成】新增 2 段讲义"
        onView={() => push("onView()")}
      />
      <div className="widget-lab-toolbar">
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            setRevision((value) => value + 1);
            push(`revision → rev-${revision + 1}`);
          }}
        >
          修订号 +1
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setRenders((value) => value + 1)}
        >
          仅重新渲染（{renders}）
        </Button>
      </div>
      <CallbackLog entries={log} />
    </>
  );
}

/** Polls every 800ms with the same revision; the notice must stay silent. */
function PollingCase({ mode }: { mode: WidgetMotionMode }) {
  const [polls, setPolls] = useState(0);
  const [revision, setRevision] = useState("rev-7");
  useEffect(() => {
    const timer = window.setInterval(() => setPolls((n) => n + 1), 800);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <>
      <Heading mode={mode} revision={revision} />
      <p className="widget-lab-mock-paragraph">
        已轮询 {polls} 次，修订号保持 {revision}。
      </p>
      <div className="widget-lab-toolbar">
        <Button
          size="sm"
          variant="outline"
          onClick={() => setRevision((value) => `${value}+`)}
        >
          让下一次轮询带回新修订号
        </Button>
      </div>
    </>
  );
}

/** Revision flips every 150ms for 1.5s; the notice appears once, no replay. */
function RapidCase({ mode }: { mode: WidgetMotionMode }) {
  const [revision, setRevision] = useState(0);
  const [running, setRunning] = useState(false);
  const ticks = useRef(0);
  useEffect(() => {
    if (!running) return;
    ticks.current = 0;
    const timer = window.setInterval(() => {
      ticks.current += 1;
      setRevision((value) => value + 1);
      if (ticks.current >= 10) setRunning(false);
    }, 150);
    return () => window.clearInterval(timer);
  }, [running]);
  return (
    <>
      <Heading mode={mode} revision={revision} />
      <div className="widget-lab-toolbar">
        <Button
          size="sm"
          variant="outline"
          disabled={running}
          onClick={() => setRunning(true)}
        >
          {running ? `快速更新中（${revision}）` : "连续更新 10 次"}
        </Button>
      </div>
    </>
  );
}

function UnknownCase({ mode }: { mode: WidgetMotionMode }) {
  const [revision, setRevision] = useState<string | null>(null);
  return (
    <>
      <Heading mode={mode} revision={revision} autoHideMs={null} />
      <p className="widget-lab-mock-paragraph">
        当前修订号：{revision ?? "未知（null）"}
      </p>
      <div className="widget-lab-toolbar">
        <Button size="sm" variant="outline" onClick={() => setRevision("r-a")}>
          设为 r-a
        </Button>
        <Button size="sm" variant="outline" onClick={() => setRevision("r-b")}>
          设为 r-b
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setRevision(null)}>
          设为 null
        </Button>
      </div>
    </>
  );
}

export function MaterialUpdateNoticeDemo() {
  return (
    <>
      <DemoCase
        title="手动更新与普通重渲染"
        note="首次修订号只作为基线；只有修订号变化才提示。“仅重新渲染”不会重播。"
      >
        {(mode) => <ManualCase mode={mode} />}
      </DemoCase>
      <DemoCase
        title="轮询同一份数据"
        note="每 800ms 用相同修订号刷新一次，提示保持静默。"
      >
        {(mode) => <PollingCase mode={mode} />}
      </DemoCase>
      <DemoCase
        title="快速连续更新"
        note="修订号每 150ms 变化一次，提示只出现一次并延长停留，不反复闪烁。"
      >
        {(mode) => <RapidCase mode={mode} />}
      </DemoCase>
      <DemoCase
        title="修订号未知 / 不自动隐藏"
        note="null 不算基线也不算变化；r-a → null → r-a 不提示，r-a → r-b 才提示。"
      >
        {(mode) => <UnknownCase mode={mode} />}
      </DemoCase>
      <DemoCase title="长文本 / 窄屏">
        {(mode) => (
          <Heading
            mode={mode}
            revision="long"
            message="【合成长文本】这是一段很长的附加说明，用来检查窄屏下的省略号与按钮不被挤出容器的表现。"
            onView={() => undefined}
          />
        )}
      </DemoCase>
    </>
  );
}
