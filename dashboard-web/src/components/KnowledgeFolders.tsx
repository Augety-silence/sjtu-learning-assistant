import { ChevronRight, Folder } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { CompiledKnowledgeSearch } from "@/lib/types";

export function KnowledgeFolders({
  folders,
  selected,
  selectedSource,
  onSelect,
  parent = "",
  source = "",
}: {
  folders: NonNullable<CompiledKnowledgeSearch["folders"]>;
  selected: string;
  selectedSource: string;
  onSelect: (source: string, path: string) => void;
  parent?: string;
  source?: string;
}) {
  return (
    <ul className="knowledge-folder-tree">
      {folders
        .filter(
          (item) =>
            (item.path.includes("/")
              ? item.path.slice(0, item.path.lastIndexOf("/"))
              : "") === parent &&
            (!source || item.source_id === source),
        )
        .map((item) => {
          const children = folders.some(
            (child) =>
              child.source_id === item.source_id &&
              child.path.startsWith(item.path + "/"),
          );
          const sameSource = selectedSource === item.source_id;
          const selectedHere = sameSource && selected === item.path;
          const label = (
            <>
              <Folder aria-hidden="true" />
              <span>{item.name}</span>
              <small>{item.count}</small>
            </>
          );
          return (
            <li key={`${item.source_id}:${item.path}`}>
              {children ? (
                <details
                  open={
                    parent === "" ||
                    (sameSource &&
                      (selectedHere || selected.startsWith(item.path + "/")))
                  }
                >
                  <summary
                    aria-current={selectedHere ? "location" : undefined}
                    onClick={() => {
                      if (!selectedHere) onSelect(item.source_id, item.path);
                    }}
                  >
                    <ChevronRight aria-hidden="true" />
                    {label}
                  </summary>
                  <KnowledgeFolders
                    folders={folders}
                    selected={selected}
                    selectedSource={selectedSource}
                    onSelect={onSelect}
                    parent={item.path}
                    source={item.source_id}
                  />
                </details>
              ) : (
                <Button
                  variant="ghost"
                  aria-pressed={selectedHere}
                  onClick={() => onSelect(item.source_id, item.path)}
                >
                  {label}
                </Button>
              )}
            </li>
          );
        })}
    </ul>
  );
}
