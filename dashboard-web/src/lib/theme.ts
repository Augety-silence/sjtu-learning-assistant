import type { ThemeMode } from "@/lib/types";

export type ResolvedTheme = Exclude<ThemeMode, "system">;

const DARK_THEME_QUERY = "(prefers-color-scheme: dark)";

function resolveTheme(
  mode: ThemeMode,
  mediaQuery?: MediaQueryList,
): ResolvedTheme {
  if (mode !== "system") return mode;
  return mediaQuery?.matches ? "dark" : "light";
}

export function applyThemeMode(mode: ThemeMode) {
  const mediaQuery = window.matchMedia?.(DARK_THEME_QUERY);
  const apply = () => {
    const resolved = resolveTheme(mode, mediaQuery);
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themeMode = mode;
    document.documentElement.style.colorScheme = resolved;
  };

  apply();
  if (mode !== "system" || !mediaQuery) return () => undefined;

  mediaQuery.addEventListener("change", apply);
  return () => mediaQuery.removeEventListener("change", apply);
}
