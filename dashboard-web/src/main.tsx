import { MotionConfig } from "motion/react";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./components/Toast";
import { Button } from "./components/ui/Button";
import { readUIRelease } from "./lib/uiRelease";
import "./index.css";
import "./aura-pages.css";
import "./ai-chat-aura.css";
import "./app-brand.css";
import "./sidebar-assistant.css";
import "./performance.css";
import "./workspace-motion.css";
import "./page-motion.css";
import "./nav-indicator.css";
import "./selection-indicator.css";
import "./v2/design.css";
import "./v2/Schedule.css";
import "./v2/features.css";
import "./v2/video.css";
import "./ui-2.css";

document.documentElement.dataset.uiRelease = readUIRelease();

// Last-resort boundary: without it, any render error unmounts the whole tree
// and the desktop window is left blank with no way to recover. React 19
// already logs caught errors through the root's default onCaughtError.
class RootErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main>
        <div className="state-box state-error state-stack" role="alert">
          <p>界面出现意外错误，本地数据不受影响。</p>
          <div className="state-actions">
            <Button type="button" onClick={() => window.location.reload()}>
              重新加载
            </Button>
          </div>
        </div>
      </main>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <MotionConfig reducedMotion="user">
        <ToastProvider>
          <App />
        </ToastProvider>
      </MotionConfig>
    </RootErrorBoundary>
  </React.StrictMode>,
);

import "./message-center.css";

import "./aime-chat.css";
import "./v2/extensions.css";
import "./v2/monochrome.css";
