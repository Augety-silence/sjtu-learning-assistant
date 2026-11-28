import { useState } from "react";
import {
  ContentSearch,
  HighlightedText,
  useContentSearch,
} from "@/components/ContentSearch";
import { findTextMatches } from "@/lib/contentSearch";

type PdfProps = {
  sourceUrl: string;
  title: string;
  pages: { page: number; text: string }[];
  note?: string;
};
export function PdfSearchPreview(props: PdfProps) {
  return (
    <ContentSearch
      resetKey={props.sourceUrl}
      note={props.note}
      className="pdf-search-preview"
    >
      <PdfSearchBody key={props.sourceUrl} {...props} />
    </ContentSearch>
  );
}
function PdfSearchBody({ sourceUrl, title, pages }: PdfProps) {
  const query = useContentSearch();
  const [page, setPage] = useState(1);
  const matched = query
    ? pages.filter((item) => findTextMatches(item.text, query).length > 0)
    : [];
  return (
    <div className="pdf-search-layout" data-searching={Boolean(query)}>
      <iframe
        src={page === 1 ? sourceUrl : `${sourceUrl.split("#")[0]}#page=${page}`}
        title={`${title} PDF 预览`}
      />
      {query && (
        <aside className="pdf-search-results" aria-label="PDF 搜索结果">
          {!matched.length && (
            <p className="content-search-empty">没有匹配的可读文字。</p>
          )}
          {matched.map((item) => (
            <section key={item.page}>
              <button
                type="button"
                className="pdf-search-page"
                onClick={() => setPage(item.page)}
                aria-label={`定位第 ${item.page} 页`}
              >
                第 {item.page} 页
              </button>
              <pre>
                <HighlightedText text={item.text} />
              </pre>
            </section>
          ))}
        </aside>
      )}
    </div>
  );
}
