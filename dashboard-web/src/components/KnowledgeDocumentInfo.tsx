import { InfoTooltip } from "@/components/ui/InfoTooltip";

export function KnowledgeDocumentInfo({
  status,
  section,
  citation,
}: {
  status: string;
  section: string;
  citation: string;
}) {
  return (
    <InfoTooltip
      className="compiled-knowledge-document-info"
      align="end"
      label="文档来源与状态"
    >
      {status} · {section}
      <br />
      来源：{citation}
    </InfoTooltip>
  );
}
