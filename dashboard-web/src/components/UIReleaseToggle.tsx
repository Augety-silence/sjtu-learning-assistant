import { setUIRelease, useUIRelease } from "@/lib/uiRelease";

export function UIReleaseToggle() {
  const release = useUIRelease();
  return (
    <button
      type="button"
      className="ui-release-toggle"
      aria-label="切换界面版本"
      title={
        release === "2" ? "切回经典界面，数据和任务保持不变" : "使用 2.0 界面"
      }
      onClick={() => setUIRelease(release === "2" ? "classic" : "2")}
    >
      {release === "2" ? "2.0 界面" : "经典界面"}
    </button>
  );
}
