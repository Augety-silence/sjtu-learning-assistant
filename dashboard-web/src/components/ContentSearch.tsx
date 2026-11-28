import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { findTextMatches, rehypeSearchHighlight } from "@/lib/contentSearch";
import "@/v2/content-search.css";

const SearchContext = createContext("");
export const useContentSearch = () => useContext(SearchContext);
export function useSearchHighlight() {
  const query = useContentSearch();
  const scope = useId();
  return useMemo(
    () =>
      [rehypeSearchHighlight, { query, scope }] as [
        typeof rehypeSearchHighlight,
        { query: string; scope: string },
      ],
    [query, scope],
  );
}

export function HighlightedText({
  text,
  query: suppliedQuery,
}: {
  text: string;
  query?: string;
}) {
  const contextQuery = useContentSearch();
  const query = suppliedQuery ?? contextQuery;
  const scope = useId();
  const matches = findTextMatches(text, query);
  if (!matches.length) return <>{text}</>;
  let offset = 0;
  const parts: ReactNode[] = [];
  matches.forEach((match, index) => {
    parts.push(text.slice(offset, match.start));
    parts.push(
      <mark
        className="content-search-hit"
        data-search-hit={`${scope}:${index}`}
        key={index}
      >
        {text.slice(match.start, match.end)}
      </mark>,
    );
    offset = match.end;
  });
  parts.push(text.slice(offset));
  return <>{parts}</>;
}

export function ContentSearch({
  children,
  label = "搜索文档",
  resetKey,
  note,
  className = "",
}: {
  children: ReactNode;
  label?: string;
  resetKey?: string;
  note?: ReactNode;
  className?: string;
}) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [count, setCount] = useState(0);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const hits = useRef<HTMLElement[][]>([]);
  const pendingScroll = useRef(false);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        !(event.metaKey || event.ctrlKey) ||
        event.key.toLowerCase() !== "f"
      )
        return;
      const activeElement = document.activeElement;
      const modal = root.current?.closest('[role="dialog"]');
      if (
        activeElement &&
        (root.current?.contains(activeElement) ||
          modal?.contains(activeElement))
      ) {
        event.preventDefault();
        field.current?.focus();
        field.current?.select();
      }
    };
    document.addEventListener("keydown", focusSearch);
    return () => document.removeEventListener("keydown", focusSearch);
  }, []);
  const reveal = (hit?: HTMLElement) => {
    if (!hit) return;
    for (
      let scroll = hit.parentElement;
      scroll && scroll !== document.body;
      scroll = scroll.parentElement
    ) {
      if (
        !/(auto|scroll)/.test(getComputedStyle(scroll).overflowY) ||
        scroll.scrollHeight <= scroll.clientHeight
      )
        continue;
      const rect = hit.getBoundingClientRect();
      const bounds = scroll.getBoundingClientRect();
      const bar = root.current?.querySelector<HTMLElement>(
        ".content-search-bar",
      );
      const top =
        Math.max(
          bounds.top,
          bar && scroll.contains(bar)
            ? bar.getBoundingClientRect().bottom
            : bounds.top,
        ) + 8;
      if (rect.top < top) scroll.scrollTop += rect.top - top;
      else if (rect.bottom > bounds.bottom - 8)
        scroll.scrollTop += rect.bottom - bounds.bottom + 8;
      break;
    }
  };
  useEffect(() => {
    setInput("");
    setQuery("");
    setActive(0);
  }, [resetKey]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(input.trim());
      setActive(0);
      pendingScroll.current = true;
    }, 150);
    return () => window.clearTimeout(timer);
  }, [input]);
  useEffect(() => {
    const collect = () => {
      const groups = new Map<string, HTMLElement[]>();
      for (const hit of root.current?.querySelectorAll<HTMLElement>(
        "mark[data-search-hit]",
      ) ?? []) {
        const key = hit.dataset.searchHit!;
        groups.set(key, [...(groups.get(key) ?? []), hit]);
      }
      hits.current = [...groups.values()];
      setCount(hits.current.length);
      setActive((index) =>
        Math.min(index, Math.max(0, hits.current.length - 1)),
      );
      paint();
    };
    const paint = () =>
      hits.current.forEach((group, index) =>
        group.forEach((hit) => {
          if (index === active) hit.setAttribute("data-search-current", "true");
          else hit.removeAttribute("data-search-current");
        }),
      );
    collect();
    const observer = new MutationObserver(collect);
    if (root.current)
      observer.observe(root.current, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [query, active]);
  useEffect(() => {
    if (pendingScroll.current && count) {
      reveal(hits.current[active]?.[0]);
      pendingScroll.current = false;
    }
  }, [count, active, query]);
  const move = (delta: number) => {
    if (!count) return;
    pendingScroll.current = true;
    setActive((index) => (index + delta + count) % count);
    // A single match still needs to be revealable.
    if (count === 1) reveal(hits.current[0]?.[0]);
  };
  return (
    <SearchContext.Provider value={query}>
      <div
        ref={root}
        className={`content-search ${className}`}
        onKeyDownCapture={(event) => {
          if (
            event.nativeEvent.isComposing ||
            event.nativeEvent.keyCode === 229
          )
            return;
          if (
            (event.metaKey || event.ctrlKey) &&
            event.key.toLowerCase() === "f"
          ) {
            event.preventDefault();
            event.stopPropagation();
            field.current?.focus();
            field.current?.select();
          } else if (event.target === field.current && event.key === "Enter") {
            event.preventDefault();
            event.stopPropagation();
            move(event.shiftKey ? -1 : 1);
          } else if (
            event.target === field.current &&
            event.key === "Escape" &&
            input
          ) {
            event.preventDefault();
            event.stopPropagation();
            setInput("");
            setQuery("");
          }
        }}
      >
        <div className="content-search-bar" role="search" aria-label={label}>
          <label className="content-search-field">
            <Search size={15} aria-hidden="true" />
            <input
              ref={field}
              type="search"
              aria-label={label}
              placeholder={label}
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
          </label>
          <span
            className="content-search-count"
            role="status"
            aria-live="polite"
          >
            {query ? (count ? `${active + 1} / ${count}` : "无匹配") : ""}
          </span>
          <button
            type="button"
            aria-label="上一个匹配"
            title="上一个（Shift + Enter）"
            disabled={!count}
            onClick={() => move(-1)}
          >
            <ChevronUp size={16} />
          </button>
          <button
            type="button"
            aria-label="下一个匹配"
            title="下一个（Enter）"
            disabled={!count}
            onClick={() => move(1)}
          >
            <ChevronDown size={16} />
          </button>
          {input && (
            <button
              type="button"
              aria-label="清除搜索"
              onClick={() => {
                setInput("");
                setQuery("");
                field.current?.focus();
              }}
            >
              <X size={15} />
            </button>
          )}
        </div>
        {note && <small className="content-search-note">{note}</small>}
        <div className="content-search-body">{children}</div>
      </div>
    </SearchContext.Provider>
  );
}
