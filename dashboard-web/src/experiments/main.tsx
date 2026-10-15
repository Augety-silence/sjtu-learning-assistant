import React from "react";
import ReactDOM from "react-dom/client";
import { WidgetLab } from "./WidgetLab";
import "../index.css";
import "./widget-lab.css";

// Standalone preview entry for experimental widgets. Not wired into App routes.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WidgetLab />
  </React.StrictMode>,
);
