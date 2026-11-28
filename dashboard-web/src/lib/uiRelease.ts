import { useSyncExternalStore } from "react";

const KEY = "sjtu-ui-release";
const EVENT = "sjtu-ui-release-change";

export function readUIRelease(): "2" | "classic" {
  try {
    if (localStorage.getItem("sjtu-ui-layout-generation") !== "2.0-full") {
      localStorage.setItem("sjtu-ui-layout-generation", "2.0-full");
      localStorage.setItem(KEY, "2");
    }
    return localStorage.getItem(KEY) === "classic" ? "classic" : "2";
  } catch {
    return "2";
  }
}

export function setUIRelease(value: "2" | "classic") {
  try {
    localStorage.setItem(KEY, value);
  } catch {
    // 无法存储时仍允许本窗口切换。
  }
  document.documentElement.dataset.uiRelease = value;
  window.dispatchEvent(new Event(EVENT));
}

function snapshot() {
  return document.documentElement.dataset.uiRelease === "classic"
    ? "classic"
    : "2";
}

function subscribe(callback: () => void) {
  window.addEventListener(EVENT, callback);
  return () => window.removeEventListener(EVENT, callback);
}

export function useUIRelease() {
  return useSyncExternalStore(subscribe, snapshot);
}
