import appLogo from "@/assets/app-logo.png";

/** One brand treatment for the workbench, mobile drawer and AI workspace. */
export function AppBrand({ onClick }: { onClick?: () => void }) {
  const content = (
    <>
      <img className="app-brand-mark" src={appLogo} alt="SJTU 学习助手标志" />
      <span className="app-brand-copy">
        <span className="app-brand-title">SJTU</span>
        <span className="app-brand-subtitle">学习助手</span>
      </span>
    </>
  );

  return onClick ? (
    <button
      type="button"
      className="app-brand"
      onClick={onClick}
      aria-label="返回学习助手"
      title="返回学习助手"
    >
      {content}
    </button>
  ) : (
    <div className="app-brand">{content}</div>
  );
}
