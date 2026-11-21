import { Info } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/Button";

export function KnowledgeDocumentInfo({
  status,
  section,
  citation,
}: {
  status: string;
  section: string;
  citation: string;
}) {
  const id = useId();
  const [dismissed, setDismissed] = useState(false);
  return (
    <span
      className="compiled-knowledge-document-info"
      data-dismissed={dismissed}
      onMouseEnter={() => setDismissed(false)}
      onFocus={() => setDismissed(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") setDismissed(true);
      }}
    >
      <Button
        variant="ghost"
        size="icon"
        aria-label="文档来源与状态"
        aria-describedby={id}
      >
        <Info aria-hidden="true" size={16} strokeWidth={1.5} />
      </Button>
      <span id={id} role="tooltip">
        {status} · {section}
        <br />
        来源：{citation}
      </span>
    </span>
  );
}
