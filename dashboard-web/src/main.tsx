import { MotionConfig } from "motion/react";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./components/Toast";
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

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <App />
      </ToastProvider>
    </MotionConfig>
  </React.StrictMode>,
);
