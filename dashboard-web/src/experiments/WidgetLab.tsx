import { FlaskConical } from "lucide-react";
import { MotionConfig } from "motion/react";
import { useEffect, useState } from "react";
import { FilterChip, StatusBadge } from "@/components/ui/CompactControls";
import { applyThemeMode } from "@/lib/theme";
import type { ThemeMode } from "@/lib/types";
import { widgetExperiments } from "./registry";

const THEMES: { mode: ThemeMode; label: string }[] = [
  { mode: "light", label: "浅色" },
  { mode: "dark", label: "深色" },
  { mode: "system", label: "跟随系统" },
];

type LabWidth = "regular" | "narrow";
type LabMotion = "user" | "always";

export function WidgetLab() {
  const [theme, setTheme] = useState<ThemeMode>("light");
  const [width, setWidth] = useState<LabWidth>("regular");
  const [reducedMotion, setReducedMotion] = useState<LabMotion>("user");

  useEffect(() => applyThemeMode(theme), [theme]);

  return (
    <MotionConfig reducedMotion={reducedMotion}>
      <div
        className="widget-lab"
        data-width={width}
        data-reduced-motion={reducedMotion}
      >
        <header className="widget-lab-header">
          <div className="widget-lab-title">
            <FlaskConical aria-hidden="true" />
            <div>
              <h1>Widget Lab · 学习小组件实验</h1>
              <p>
                独立预览页，不挂载到正式路由。所有内容均为
                <strong>合成数据</strong>，不请求任何真实接口。
              </p>
            </div>
            <StatusBadge tone="warning">合成数据 · 实验中</StatusBadge>
          </div>
          <div className="widget-lab-controls">
            <fieldset>
              <legend>主题</legend>
              {THEMES.map((item) => (
                <FilterChip
                  key={item.mode}
                  pressed={theme === item.mode}
                  onClick={() => setTheme(item.mode)}
                >
                  {item.label}
                </FilterChip>
              ))}
            </fieldset>
            <fieldset>
              <legend>宽度</legend>
              <FilterChip
                pressed={width === "regular"}
                onClick={() => setWidth("regular")}
              >
                常规
              </FilterChip>
              <FilterChip
                pressed={width === "narrow"}
                onClick={() => setWidth("narrow")}
              >
                窄屏 340px
              </FilterChip>
            </fieldset>
            <fieldset>
              <legend>动态效果</legend>
              <FilterChip
                pressed={reducedMotion === "user"}
                onClick={() => setReducedMotion("user")}
              >
                跟随系统
              </FilterChip>
              <FilterChip
                pressed={reducedMotion === "always"}
                onClick={() => setReducedMotion("always")}
              >
                模拟减少动态效果
              </FilterChip>
            </fieldset>
          </div>
          {widgetExperiments.length > 0 && (
            <nav className="widget-lab-nav" aria-label="实验目录">
              {widgetExperiments.map((experiment) => (
                <a key={experiment.id} href={`#${experiment.id}`}>
                  {experiment.title}
                </a>
              ))}
            </nav>
          )}
        </header>
        <main className="widget-lab-main">
          {widgetExperiments.length === 0 && (
            <p className="widget-lab-empty">尚未登记实验组件。</p>
          )}
          {widgetExperiments.map(({ id, title, summary, futureSlot, Demo }) => (
            <article
              key={id}
              id={id}
              className="widget-lab-experiment"
              aria-labelledby={`${id}-title`}
            >
              <header>
                <h2 id={`${id}-title`}>{title}</h2>
                <p>{summary}</p>
                <p className="widget-lab-slot">未来接入点：{futureSlot}</p>
              </header>
              <Demo />
            </article>
          ))}
        </main>
      </div>
    </MotionConfig>
  );
}
