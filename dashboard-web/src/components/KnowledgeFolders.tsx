import { ChevronRight, Folder } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/Button";
import type { CompiledKnowledgeSearch } from "@/lib/types";

function FolderBranch({
  initiallyOpen,
  selected,
  label,
  onSelect,
  children,
}: {
  initiallyOpen: boolean;
  selected: boolean;
  label: ReactNode;
  onSelect: () => void;
  children: ReactNode;
}) {
  // 展开属于用户操作，不跟随查询／选中状态反复写入 open 属性。
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary
        aria-current={selected ? "location" : undefined}
        onClick={() => {
          if (!selected) onSelect();
        }}
      >
        <ChevronRight aria-hidden="true" />
        {label}
      </summary>
      {children}
    </details>
  );
}

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
                <FolderBranch
                  initiallyOpen={
                    parent === "" ||
                    (sameSource &&
                      (selectedHere || selected.startsWith(item.path + "/")))
                  }
                  selected={selectedHere}
                  label={label}
                  onSelect={() => onSelect(item.source_id, item.path)}
                >
                  <KnowledgeFolders
                    folders={folders}
                    selected={selected}
                    selectedSource={selectedSource}
                    onSelect={onSelect}
                    parent={item.path}
                    source={item.source_id}
                  />
                </FolderBranch>
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
