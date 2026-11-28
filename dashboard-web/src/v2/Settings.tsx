import {
  Archive,
  ChartNoAxesCombined,
  MessageSquare,
  Palette,
  Plug,
  RefreshCw,
  Settings,
} from "lucide-react";
import { Children, isValidElement, type ReactNode, useState } from "react";
import { ContentReveal } from "@/components/ContentReveal";
import { TokenUsageDashboard } from "@/components/TokenUsageDashboard";
import { useUIRelease } from "@/lib/uiRelease";

const sections = [
  { id: "appearance-title", label: "外观", icon: Palette },
  { id: "usage", label: "模型用量", icon: ChartNoAxesCombined },
  { id: "configuration-title", label: "账户与连接", icon: Plug },
  { id: "archive-settings-title", label: "本地存储", icon: Archive },
  { id: "debug-feedback-title", label: "诊断与反馈", icon: MessageSquare },
  { id: "update-settings-title", label: "软件更新", icon: RefreshCw },
];
export function V2SettingsSurface({ children }: { children: ReactNode }) {
  const release = useUIRelease();
  const [selected, setSelected] = useState("appearance-title");
  if (release !== "2")
    return (
      <ContentReveal className="section-stack settings-page" afterLoad>
        {children}
      </ContentReveal>
    );
  return (
    <div className="v2-settings-workspace">
      <nav aria-label="设置分类">
        <header>
          <Settings />
          <strong>设置分类</strong>
        </header>
        {sections.map(({ id, label, icon: Icon }) => (
          <button
            type="button"
            key={id}
            aria-current={selected === id ? "page" : undefined}
            onClick={() => setSelected(id)}
          >
            <Icon />
            {label}
          </button>
        ))}
      </nav>
      <div className="v2-settings-content">
        {Children.toArray(children).map((child) => {
          if (!isValidElement<{ "aria-labelledby"?: string }>(child))
            return child;
          let id = child.props["aria-labelledby"];
          if (child.type === TokenUsageDashboard) id = "usage";
          return id ? (
            <div
              key={child.key ?? id}
              hidden={selected !== id}
              className="v2-settings-section"
            >
              {child}
            </div>
          ) : (
            child
          );
        })}
      </div>
    </div>
  );
}
