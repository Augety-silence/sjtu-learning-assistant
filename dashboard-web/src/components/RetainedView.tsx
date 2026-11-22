import {
  type ReactNode,
  type RefObject,
  useLayoutEffect,
  useState,
} from "react";
import { createPortal } from "react-dom";

/** Keep a visited view's DOM and media buffer alive across workspace remounts.
 * Inactive content is detached, not left focusable or audible behind the page. */
export function RetainedView({
  active,
  target,
  children,
}: {
  active: boolean;
  target: RefObject<HTMLDivElement | null>;
  children: ReactNode;
}) {
  const [host] = useState(() => document.createElement("div"));
  const [visited, setVisited] = useState(active);
  useLayoutEffect(() => {
    if (active) {
      setVisited(true);
      target.current?.append(host);
    } else host.remove();
    return () => host.remove();
  }, [active, host, target]);
  return visited ? createPortal(children, host) : null;
}
