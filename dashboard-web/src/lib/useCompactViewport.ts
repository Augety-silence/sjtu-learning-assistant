import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 599px)";

function readCompactViewport() {
  if (typeof window === "undefined") return false;
  return typeof window.matchMedia === "function"
    ? window.matchMedia(MOBILE_QUERY).matches
    : window.innerWidth < 600;
}

export function useCompactViewport() {
  const [compact, setCompact] = useState(readCompactViewport);

  useEffect(() => {
    const update = () => setCompact(readCompactViewport());
    const media =
      typeof window.matchMedia === "function"
        ? window.matchMedia(MOBILE_QUERY)
        : null;

    media?.addEventListener?.("change", update);
    window.addEventListener("resize", update);
    update();
    return () => {
      media?.removeEventListener?.("change", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  return compact;
}
